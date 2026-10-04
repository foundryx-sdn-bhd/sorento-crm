"""Product management models."""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base import CompanyScopedMixin
import uuid

# Forward references for relationships
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.models.inventory import Stock, StockBatch
    from app.models.procurement import ProductSupplier, InboundShipmentLine, SPOAllocation, PickingLine
    from app.models.marketing import PromotionProduct
    from app.models.resources import Attachment


class ProductCategory(Base, CompanyScopedMixin):
    __tablename__ = "product_categories"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite unique index in
    # __table_args__ below. A bare unique=True here would make create_all build
    # the old global product_categories_category_code_key and reject the same
    # code in a second company (fix round 4).
    category_code = Column(String(50), nullable=False)
    category_name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    parent_category_id = Column(UUID(as_uuid=False), ForeignKey("product_categories.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    display_order = Column(Integer, default=0, nullable=True)
    # The class/brand signal decoded out of category_code (SRT-KS -> Kitchen Sink /
    # Sorento). category_name is a verbatim copy of category_code on every live row,
    # so without these the highest-coverage ranking signal in the catalog is
    # unreadable. Populated by app/services/product_class_signal.py, never by parsing
    # the code at query time. A NULL class_label means "not classified", which is a
    # reportable state, not a default.
    class_label = Column(String(100), nullable=True)
    brand_hint = Column(String(100), nullable=True)
    # Customer phrasings for the class ("sinki", "dapur"), matched case-insensitively
    # alongside class_label so catalog language and customer language can differ.
    search_synonyms = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # False for categories with no class meaning (MISC, PROJECT, SRTPART, VD) so they
    # cannot masquerade as a searchable class.
    is_searchable = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    # Chatbot stock ask v2 S1 (PLAN-chatbot-stock-ask-v2-24sep.md, R2): X (max quantity
    # the assistant may confirm) and Y (days added to a shipment ETA) for every product
    # in this category, unless the product overrides them. NULL means "not opted in" -
    # `app.services.stock_ask_limits.effective()` resolves it to 0. No parent-category
    # walk: only a product's OWN category is ever consulted.
    chatbot_max_qty = Column(Integer, nullable=True)
    chatbot_eta_offset_days = Column(Integer, nullable=True)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)

    parent = relationship("ProductCategory", remote_side=[id], back_populates="children")
    children = relationship("ProductCategory", back_populates="parent")
    products = relationship("Product", back_populates="category")
    
    __table_args__ = (
        Index(
            "uq_product_categories_company_category_code",
            "company_id", "category_code",
            unique=True,
        ),
        Index("ix_product_categories_parent_category_id", "parent_category_id"),
        Index("ix_product_categories_is_active", "is_active"),
        # Should fix 2 (reviewer pass, PR #1221, 85c2e9e7): matches
        # sa2_0001_xy_columns's CHECK constraints by name, so `create_all`
        # (every blank-schema test fixture, bootstrap_env) and the migration
        # (prod) agree - `create_all` built these columns with no CHECK at all
        # before this.
        CheckConstraint(
            "chatbot_max_qty >= 0", name="ck_product_categories_chatbot_max_qty_non_negative"
        ),
        CheckConstraint(
            "chatbot_eta_offset_days >= 0",
            name="ck_product_categories_chatbot_eta_offset_days_non_negative",
        ),
    )


class Brand(Base, CompanyScopedMixin):
    __tablename__ = "brands"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite unique index in
    # __table_args__ below. A bare unique=True here would make create_all build
    # the old global brands_brand_code_key and reject the same code in a second
    # company (fix round 4).
    brand_code = Column(String(50), nullable=False)
    brand_name = Column(String(150), nullable=False)
    manufacturer = Column(String(150), nullable=True)
    website = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    logo_url = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    # Visibility codes overlapping `contact_access_types.code`. Used by the
    # resolver's promotion-domain product fallback to scope product search to
    # brands the active contact can see. Default mirrors the Attachment +
    # Promotion default so existing brands stay broadly visible.
    access_levels = Column(JSONB, nullable=False, server_default='["dealer","end_user"]')
    # PLAN-brand-flows-to-purchasing.md (owner ruling 22 Sep 2026): a brand bought
    # locally by CS on its own (TP Enterprise is the named one) never reaches
    # purchasing - `app.services.scm.supply_origin.buy_origin_by_product` reads this
    # BEFORE the local-buy-routing toggle and answers "local" for its products
    # whatever the toggle state. Default true so nothing changes until an admin
    # flips a brand.
    flows_to_purchasing = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    # Owner ruling R1 on PR #833 (27 Sep 2026, "weights as brand preference instead of
    # switch"): when a customer names no brand, a counted set answers the highest weighted
    # brand it reaches first and names the other brands in weight order. 0 = no preference.
    # Seeded Sorento 1.5 by migration bcw_0001; edited on Master Data > Brands.
    chatbot_weight = Column(Numeric(6, 2), nullable=False, default=0, server_default=text("0"))
    # "Customers can ask for this brand" (#1286, D3). False for OTHERS and NO LOGO,
    # which is how the catalogue records the ABSENCE of a brand: the understanding
    # model is never offered them, and a single word never binds one in search (a
    # multi-word name still binds on the full phrase, so "no logo kitchen sink" works).
    # Same name and meaning as `product_categories.is_searchable`. It replaced the
    # `excluded_values` of the removed Brand specification.
    is_searchable = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)

    products = relationship("Product", back_populates="brand")

    __table_args__ = (
        Index(
            "uq_brands_company_brand_code", "company_id", "brand_code", unique=True
        ),
        Index("ix_brands_is_active", "is_active"),
        Index("ix_brands_access_levels", "access_levels", postgresql_using="gin"),
    )


class ItemType(Base, CompanyScopedMixin):
    """AutoCount `Item.ItemType` (MISC, PROJECT, WASTE, KITCHEN SINK, OMEX, ...) as
    reference data, the brand rule exactly: back-created code = name = raw value by
    the products ingest when a pushed `item_type_code` does not exist yet
    (`product_rules.ensure_reference`). PLAN-item-type-crm-3oct.md D1."""
    __tablename__ = "item_types"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_type_code = Column(String(50), nullable=False)
    item_type_name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        Index(
            "uq_item_types_company_item_type_code",
            "company_id", "item_type_code",
            unique=True,
        ),
    )


class UnitOfMeasure(Base, CompanyScopedMixin):
    __tablename__ = "units_of_measure"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite unique index in
    # __table_args__ below. A bare unique=True here would make create_all build
    # the old global units_of_measure_uom_code_key and reject the same code in a
    # second company (fix round 4).
    uom_code = Column(String(50), nullable=False)
    uom_name = Column(String(150), nullable=False)
    base_uom_id = Column(UUID(as_uuid=False), ForeignKey("units_of_measure.id", ondelete="SET NULL"), nullable=True)
    conversion_factor = Column(Numeric(10, 4), nullable=True)
    # Canonical divisibility of the unit (front-planning plan 6.4, AC-F12): how many
    # fractional digits a quantity in this unit may carry. `EA` is 0 and refuses 2.5,
    # `kg` at 3 accepts it. It is a property of the UNIT, not of SCM arithmetic, and it
    # is never inferred from `conversion_factor`. 0 is the rollout fallback, so a unit
    # nobody has classified behaves as whole units rather than as unbounded precision.
    decimal_places = Column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)
    
    base_uom = relationship("UnitOfMeasure", remote_side=[id], back_populates="alternate_uoms")
    alternate_uoms = relationship("UnitOfMeasure", back_populates="base_uom")
    products = relationship("Product", back_populates="base_uom", foreign_keys="[Product.base_uom_id]")
    
    __table_args__ = (
        Index(
            "uq_units_of_measure_company_uom_code",
            "company_id", "uom_code",
            unique=True,
        ),
        Index("ix_units_of_measure_base_uom_id", "base_uom_id"),
        Index("ix_units_of_measure_is_active", "is_active"),
        CheckConstraint(
            "decimal_places >= 0 AND decimal_places <= 4",
            name="ck_units_of_measure_decimal_places",
        ),
    )


class Product(Base, CompanyScopedMixin):
    """Product model. Set __audit_track__ = True for automatic audit logging of changes."""
    __tablename__ = "products"
    __audit_track__ = True
    __audit_entity_type__ = "product"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): the composite unique index
    # lives in __table_args__. A bare unique=True here would make create_all build
    # the old global products_product_code_key and reject the same code in a second
    # company.
    product_code = Column(String(100), nullable=False)
    product_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # AutoCount `Item.Desc2` (migration 475, D4). Stored separately, never
    # concatenated into `description` - the xlsx import keeps doing that on
    # its own `desc2` column handling; the ESB masters push writes here.
    remark = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=False), ForeignKey("product_categories.id"), nullable=False)
    brand_id = Column(UUID(as_uuid=False), ForeignKey("brands.id", ondelete="SET NULL"), nullable=True)
    # AutoCount ItemType (migration item_type_0001). Set only by the products ingest.
    # Unrelated to `item_type` below, the CRM's own product/bundle/service/other enum.
    item_type_id = Column(
        UUID(as_uuid=False), ForeignKey("item_types.id", ondelete="SET NULL"), nullable=True,
        index=True,
    )
    # Self-referential variant graph. A variant points at its (longest existing
    # boundary-prefix) parent product; a base has variant_of_id IS NULL. Deleting
    # a parent SET-NULLs its children (never blocks) - derivation re-anchors them
    # to the next existing ancestor. See app/services/variant_link_service.py and
    # docs/plans/PLAN-suggest-on-miss-variant-graph.md §1.
    variant_of_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    # Manual-curation flag. When true, the auto-derivation (reconcile_variant_links /
    # _adopt_orphans / backfill) must NOT re-derive or re-point this row's variant
    # link - a hand-set parent (or hand-cleared link) is sticky until "reset to auto"
    # clears the flag. See docs/plans/PLAN-variant-manual-curation.md.
    variant_link_manual = Column(Boolean, nullable=False, server_default="false", default=False)
    base_uom_id = Column(UUID(as_uuid=False), ForeignKey("units_of_measure.id"), nullable=False)
    item_type = Column(String(50), nullable=True)
    # CRM-owned (PLAN D14, price-tag-feedback-r2): manual entry on the product
    # master, printed by the tag designer's barcode layer. AutoCount's
    # `bar_code` overwrites it only when the incoming value is non-empty - see
    # `master_ingest_service._product_columns`.
    barcode = Column(String(100), nullable=True)
    # PLAN-price-tag-r10.md S4: staff-authored copy for the price tag, never
    # touched by the AutoCount masters push (`remark` above is the same shape
    # for AutoCount's own text - the two never overwrite each other).
    price_tag_description = Column(Text, nullable=True)
    list_price = Column(Numeric(12, 2), nullable=False)
    cost_price = Column(Numeric(12, 2), nullable=True)
    invoice_price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), nullable=False, server_default="MYR", default="MYR")
    weight = Column(Numeric(10, 3), nullable=True)
    dimensions_length = Column(Numeric(10, 2), nullable=True)
    dimensions_width = Column(Numeric(10, 2), nullable=True)
    dimensions_height = Column(Numeric(10, 2), nullable=True)
    warranty_months = Column(Integer, nullable=True)
    has_serial_tracking = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    has_batch_tracking = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    reorder_level = Column(Integer, nullable=True)
    reorder_quantity = Column(Integer, nullable=True)
    # Chatbot stock ask v2 S1 (PLAN-chatbot-stock-ask-v2-24sep.md, R2): X (max quantity
    # the assistant may confirm) and Y (days added to a shipment ETA). NULL means "not
    # set on this product" - `app.services.stock_ask_limits.effective()` falls back to
    # the product's own category, then to 0. Overrides the category value when set; no
    # parent-category walk.
    chatbot_max_qty = Column(Integer, nullable=True)
    chatbot_eta_offset_days = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    # Whether the chatbot may answer with this product. Placeholder rows that exist
    # only for order / sample bookkeeping ("SORENTO", "SORENTOBAG") must stay
    # is_active because orders reference them, so is_active is not the lever.
    # Read through `chat_searchable_products()` below, never inline.
    is_searchable = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    is_discontinued = Column(Boolean, default=False, nullable=False, server_default="false")
    # S5 (PLAN-reorder-feedback-9sep.md, G3 ruling): the buyer's own switch to keep a
    # placeholder code (`**NEW`, `**SPARE PART`, ...) out of the reorder engine. Defaults
    # false for EVERY product with no backfill - the buyer flips it by hand, one at a time.
    exclude_from_planning = Column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    # Discontinued-notification watermark. NULL = not yet reported by the batch cron
    # (cron-eligible while is_discontinued is True). Stamped with the run time when a
    # batch goes out; cleared back to NULL whenever is_discontinued flips True->False
    # so a later re-discontinuation is reported again.
    discontinued_notified_at = Column(DateTime(timezone=False), nullable=True)
    # Stable id of the batch this product was reported in. Deep-linked from the
    # notification to the product list filtered to exactly that batch.
    discontinued_notify_batch_id = Column(UUID(as_uuid=False), nullable=True)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    updated_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)

    category = relationship("ProductCategory", back_populates="products")
    brand = relationship("Brand", back_populates="products")
    variant_of = relationship("Product", remote_side=[id], backref="variants")
    base_uom = relationship("UnitOfMeasure", back_populates="products", foreign_keys=[base_uom_id])
    product_suppliers = relationship(
        "ProductSupplier",
        back_populates="product",
        foreign_keys="ProductSupplier.product_id",
        passive_deletes=True,
    )
    stock = relationship("Stock", back_populates="product", passive_deletes=True)
    stock_batches = relationship("StockBatch", back_populates="product", passive_deletes=True)
    promotion_products = relationship(
        "PromotionProduct",
        back_populates="product",
        passive_deletes=True,
    )
    inbound_shipment_lines = relationship("InboundShipmentLine", back_populates="product")
    spo_allocations = relationship(
        "SPOAllocation",
        back_populates="product",
        passive_deletes=True,
    )
    picking_lines = relationship("PickingLine", back_populates="product")
    product_attachments = relationship(
        "ProductAttachment",
        back_populates="product",
        passive_deletes=True,
    )
    
    __table_args__ = (
        Index("uq_products_company_product_code", "company_id", "product_code", unique=True),
        Index("ix_products_category_id", "category_id"),
        Index("ix_products_brand_id", "brand_id"),
        Index("ix_products_base_uom_id", "base_uom_id"),
        Index("ix_products_is_active", "is_active"),
        Index("ix_products_product_code", "product_code"),
        Index("ix_products_barcode", "barcode"),
        Index("ix_products_variant_of_id", "variant_of_id"),
        Index(
            "ix_products_variant_link_manual",
            "variant_link_manual",
            postgresql_where=text("variant_link_manual = true"),
        ),
        Index("ix_products_discontinued_pending", "is_discontinued", "discontinued_notified_at"),
        Index("ix_products_discontinued_notify_batch_id", "discontinued_notify_batch_id"),
        # Should fix 2 (reviewer pass, PR #1221, 85c2e9e7): matches
        # sa2_0001_xy_columns's CHECK constraints by name, so `create_all`
        # (every blank-schema test fixture, bootstrap_env) and the migration
        # (prod) agree - `create_all` built these columns with no CHECK at all
        # before this.
        CheckConstraint(
            "chatbot_max_qty >= 0", name="ck_products_chatbot_max_qty_non_negative"
        ),
        CheckConstraint(
            "chatbot_eta_offset_days >= 0",
            name="ck_products_chatbot_eta_offset_days_non_negative",
        ),
    )


def chat_searchable_products():
    """The one predicate every chat-facing product read applies.

    A product is a chat answer unless it says otherwise: its own `is_searchable`
    is False, or it sits in a category with no class meaning
    (`ProductCategory.is_searchable` False - MISC, PROJECT, SRTPART, VD ...).
    Both halves fail closed only on an explicit False; anything else is today's
    behaviour, so the response stays byte-identical for every searchable row.

    The category half is a Core subquery on the table, not the ORM entity, on
    purpose. Categories are per company and 498 products point at a category
    row owned by the OTHER company; an ORM read of `ProductCategory` would be
    company-scoped by the `do_orm_execute` listener, that row would vanish from
    the subquery, and the product would slip back in. The rule follows the FK.
    The product side stays an ordinary ORM column so the listener still scopes
    every probe that uses this.
    """
    from sqlalchemy import and_, select

    categories = ProductCategory.__table__
    non_searchable_categories = select(categories.c.id).where(
        categories.c.is_searchable.is_(False)
    )
    return and_(
        Product.is_searchable.isnot(False),
        Product.category_id.notin_(non_searchable_categories),
    )


class ProductAttachment(Base, CompanyScopedMixin):
    __tablename__ = "product_attachments"
    __audit_skip__ = "link table, 259 to 4,399 rows a day (measured 27 Sep 2026, review B3)"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    attachment_id = Column(UUID(as_uuid=False), ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False)
    is_primary = Column(Boolean, default=False, nullable=True)
    sort_order = Column(Integer, nullable=True)
    access_levels = Column(JSONB, nullable=False, server_default='["dealer","end_user"]')
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=True)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    synced_to_excel = Column(Boolean, default=False, nullable=True)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)
    updated_at = Column(DateTime(timezone=False), nullable=True)
    # Which PRODUCT SET fanned this link out, if any. NULL means a person or an
    # exact product code made it. Without this, a flyer linked by set code cannot
    # be cleaned up when the set's membership changes.
    linked_via_set_id = Column(
        UUID(as_uuid=False), ForeignKey("product_sets.id", ondelete="SET NULL"), nullable=True
    )
    
    product = relationship("Product", back_populates="product_attachments")
    attachment = relationship("Attachment", foreign_keys=[attachment_id])
    
    __table_args__ = (
        Index("ix_product_attachments_product_id", "product_id"),
        Index("ix_product_attachments_attachment_id", "attachment_id"),
        Index("uq_product_attachment", "product_id", "attachment_id", unique=True),
        Index("ix_product_attachments_access_levels", "access_levels", postgresql_using="gin"),
        # At most ONE chosen brochure image per product. `is_primary` is what
        # decides a catalogue tile's photo (app/services/dealer_kit/product_images.py
        # orders by it), so two rows flagged at once would put that photo back at
        # the mercy of row order - the exact defect the picker exists to remove.
        # Partial, because the overwhelming majority of rows are not primary and
        # a full unique index would forbid a product having two unchosen photos.
        Index(
            "uq_product_attachment_primary",
            "company_id",
            "product_id",
            unique=True,
            postgresql_where=text("is_primary IS TRUE"),
        ),
    )
