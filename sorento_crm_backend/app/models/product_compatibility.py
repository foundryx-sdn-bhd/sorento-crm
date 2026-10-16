"""Accessory <-> host compatibility links (WC-SEAT-COMPAT, MIRROR-LINKS).

A link says "accessory `seat_cover_product_id` fits host SKU `wc_product_id`", same company.
The accessory end is a seat cover, a mirror or a shelf; the host end is a WC or a cabinet.
The column names predate mirrors and shelves and are not renamed. `kind` lives on the link
and is never inferred from a code prefix (SRTSV310-DIY and SRTSV334-RED are not flyer
shelves). Always one SKU to one SKU: no model-wide link and no inheritance, so a WC SKU has a cover
only when a person linked it (owner ruling 12 Oct: match only what the flyer prints,
fabricate nothing). A WC SKU with no link has no answer.

A spec cannot hold this (one value per SKU, `product_spec.py`), `product_alternatives`
means "use this instead" and `product_sets` means "made of", so it is its own table.
Plans: documentation/plans/products/PLAN-wc-seat-compat-12oct.md,
documentation/plans/products/PLAN-mirror-shelf-links-14oct.md.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import CompanyScopedMixin


class ProductCompatibility(Base, CompanyScopedMixin):
    __tablename__ = "product_compatibility"
    __audit_track__ = True
    __audit_entity_type__ = "product_compatibility"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # CASCADE both ways: a link is meaningless once either end is gone, and it must never
    # block a product delete.
    seat_cover_product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    wc_product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    # flyer (the owner-reviewed flyer import) | manual (a person on the product page).
    source = Column(String(20), nullable=False, default="manual", server_default="manual")
    # seat_cover | mirror | shelf. Existing rows are seat covers.
    kind = Column(String(20), nullable=False, default="seat_cover", server_default="seat_cover")
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_by = Column(UUID(as_uuid=False), nullable=True)

    seat_cover = relationship("Product", foreign_keys=[seat_cover_product_id])
    wc_product = relationship("Product", foreign_keys=[wc_product_id])

    __table_args__ = (
        Index(
            "uq_product_compatibility_pair_kind",
            "seat_cover_product_id",
            "wc_product_id",
            "kind",
            unique=True,
        ),
        Index("ix_product_compatibility_wc", "wc_product_id"),
        CheckConstraint(
            "seat_cover_product_id <> wc_product_id", name="ck_product_compatibility_not_self"
        ),
        CheckConstraint(
            "source IN ('flyer', 'manual')", name="ck_product_compatibility_source"
        ),
        CheckConstraint(
            "kind IN ('seat_cover', 'mirror', 'shelf')", name="ck_product_compatibility_kind"
        ),
    )
