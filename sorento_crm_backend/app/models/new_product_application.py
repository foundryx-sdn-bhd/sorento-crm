"""New Product Application (NEW-PRODUCT-APP): asking purchasing to open new item codes.

One header, many lines (one line per code wanted). Raised by staff or by a portal contact,
usually from a responded stock inquiry; approved by a holder of
`procurement.new_product_applications.approve`; then purchasing opens each code in
AutoCount and LINKS the synced product to the line (the CRM never creates the code, see
the card). Every link is picked by a person: nothing here matches names or codes.

Statuses are a plain column (moves coded in `new_product_application_service`), like the
stock inquiry and the purchase request: draft, submitted, approved, rejected, completed,
cancelled.

Card: documentation/plans/procurement/CARD-new-product-application-17oct.md.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import CompanyScopedMixin


def _uuid_str() -> str:
    return str(uuid.uuid4())


STATUSES = ("draft", "submitted", "approved", "rejected", "completed", "cancelled")
SOURCES = ("staff", "portal")


class NewProductApplication(Base, CompanyScopedMixin):
    """One application (`NPA-000001`)."""

    __tablename__ = "new_product_applications"
    __audit_entity_type__ = "new_product_application"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    #: NOT NULL here although the mixin leaves it nullable: the insert stamp fills it, and the
    #: numbering lock is keyed on it.
    company_id = Column(UUID(as_uuid=False), ForeignKey("companies.id"), nullable=False, index=True)
    application_no = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="draft", server_default="draft")
    source = Column(String(10), nullable=False, default="staff", server_default="staff")

    applicant_id = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: The portal contact who raised it (NULL for staff). Owner of a portal application.
    contact_id = Column(Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True)
    space_id = Column(Text, nullable=True)

    stock_inquiry_id = Column(
        UUID(as_uuid=False), ForeignKey("stock_inquiries.id", ondelete="SET NULL"), nullable=True
    )
    customer_id = Column(
        UUID(as_uuid=False), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    #: Typed when the customer has no debtor code; copied as TEXT from the inquiry, never matched.
    customer_name = Column(String(255), nullable=True)
    project_name = Column(String(255), nullable=True)
    needed_by = Column(Date, nullable=True)
    remark = Column(Text, nullable=True)

    submitted_at = Column(DateTime(timezone=False), nullable=True)
    decided_at = Column(DateTime(timezone=False), nullable=True)
    decided_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reject_reason = Column(Text, nullable=True)
    #: The latest "What changed, and why?" (the audit trail keeps every earlier one).
    revise_reason = Column(Text, nullable=True)
    cancelled_at = Column(DateTime(timezone=False), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    lines = relationship(
        "NewProductApplicationLine",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="NewProductApplicationLine.line_no",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "application_no", name="uq_new_product_application_no"),
        CheckConstraint(
            "status IN ('draft','submitted','approved','rejected','completed','cancelled')",
            name="ck_new_product_applications_status",
        ),
        CheckConstraint("source IN ('staff','portal')", name="ck_new_product_applications_source"),
        Index("ix_new_product_applications_status", "status"),
        Index("ix_new_product_applications_applicant", "applicant_id"),
        Index("ix_new_product_applications_contact", "contact_id"),
        Index("ix_new_product_applications_inquiry", "stock_inquiry_id"),
    )


class NewProductApplicationLine(Base):
    """One code wanted. Scoped through its application (no company_id of its own)."""

    __tablename__ = "new_product_application_lines"
    __audit_entity_type__ = "new_product_application_line"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    application_id = Column(
        UUID(as_uuid=False),
        ForeignKey("new_product_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_no = Column(Integer, nullable=False)
    proposed_code = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    further_description = Column(Text, nullable=True)
    category_id = Column(
        UUID(as_uuid=False), ForeignKey("product_categories.id", ondelete="SET NULL"), nullable=True
    )
    base_uom_id = Column(
        UUID(as_uuid=False), ForeignKey("units_of_measure.id", ondelete="SET NULL"), nullable=True
    )
    brand_id = Column(UUID(as_uuid=False), ForeignKey("brands.id", ondelete="SET NULL"), nullable=True)
    item_type_id = Column(
        UUID(as_uuid=False), ForeignKey("item_types.id", ondelete="SET NULL"), nullable=True
    )
    supplier_id = Column(
        UUID(as_uuid=False), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    supplier_item_code = Column(String(100), nullable=True)
    cost_price = Column(Numeric(12, 2), nullable=True)
    selling_price = Column(Numeric(12, 2), nullable=True)
    expected_qty = Column(Numeric(12, 3), nullable=True)
    remark = Column(Text, nullable=True)

    #: Purchasing's explicit link to the product AutoCount sent (card rule 6).
    opened_product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    #: "Not opening" reason. A line is resolved when it has a product OR a reason, never both.
    closed_reason = Column(Text, nullable=True)
    resolved_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime(timezone=False), nullable=True)

    application = relationship("NewProductApplication", back_populates="lines")

    __table_args__ = (
        CheckConstraint(
            "opened_product_id IS NULL OR closed_reason IS NULL",
            name="ck_new_product_application_lines_one_outcome",
        ),
        CheckConstraint(
            "cost_price IS NULL OR cost_price >= 0", name="ck_new_product_application_lines_cost"
        ),
        CheckConstraint(
            "selling_price IS NULL OR selling_price >= 0",
            name="ck_new_product_application_lines_selling",
        ),
        CheckConstraint(
            "expected_qty IS NULL OR expected_qty > 0", name="ck_new_product_application_lines_qty"
        ),
        Index("ix_new_product_application_lines_application", "application_id"),
        Index("ix_new_product_application_lines_product", "opened_product_id"),
    )


APPLICATION_NO_PREFIX = "NPA-"
APPLICATION_NO_DIGITS = 6


def next_application_no(bind, company_id) -> str:
    """Highest issued number for the company plus one (never the count, so a gap left by a
    deleted draft is not refilled), under a per-company advisory lock. Copied from
    `app.models.sample_request.next_request_no`."""
    table = NewProductApplication.__table__
    bind.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"new_product_application:{company_id}"},
    )
    latest = bind.execute(
        select(table.c.application_no)
        .where(
            table.c.company_id == company_id,
            table.c.application_no.like(f"{APPLICATION_NO_PREFIX}%"),
        )
        .order_by(func.length(table.c.application_no).desc(), table.c.application_no.desc())
        .limit(1)
    ).scalar()
    tail = (latest or "")[len(APPLICATION_NO_PREFIX):]
    highest = int(tail) if tail.isdigit() else 0
    return f"{APPLICATION_NO_PREFIX}{highest + 1:0{APPLICATION_NO_DIGITS}d}"


@event.listens_for(NewProductApplication, "before_insert")
def _stamp_application_no(_mapper, connection, target) -> None:
    if getattr(target, "application_no", None):
        return
    target.application_no = next_application_no(connection, target.company_id)
