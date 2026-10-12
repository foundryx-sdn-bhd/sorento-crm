"""Seat cover <-> WC compatibility links (WC-SEAT-COMPAT).

A link says "seat cover `seat_cover_product_id` fits WC SKU `wc_product_id`", same company.
Always one SKU to one SKU: no model-wide link and no inheritance, so a WC SKU has a cover
only when a person linked it (owner ruling 12 Oct: match only what the flyer prints,
fabricate nothing). A WC SKU with no link has no answer.

A spec cannot hold this (one value per SKU, `product_spec.py`), `product_alternatives`
means "use this instead" and `product_sets` means "made of", so it is its own table.
Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
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
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_by = Column(UUID(as_uuid=False), nullable=True)

    seat_cover = relationship("Product", foreign_keys=[seat_cover_product_id])
    wc_product = relationship("Product", foreign_keys=[wc_product_id])

    __table_args__ = (
        Index(
            "uq_product_compatibility_pair",
            "seat_cover_product_id",
            "wc_product_id",
            unique=True,
        ),
        Index("ix_product_compatibility_wc", "wc_product_id"),
        CheckConstraint(
            "seat_cover_product_id <> wc_product_id", name="ck_product_compatibility_not_self"
        ),
        CheckConstraint(
            "source IN ('flyer', 'manual')", name="ck_product_compatibility_source"
        ),
    )
