"""Sample Request: the consignment requisition that replaces the paper form (SR-CORE).

One header, many product lines. A salesperson raises it from the portal or from a
project's tab, and the coordinator works it from one queue up to Delivered. Stock
allocation, borrow approval and returns are later slices; this table only leaves the
columns they will need (`return_due`, the Processing -> Customer confirmed gap).

In the `projects` schema because it belongs to the Project Sales module (PRINCIPLES,
"Modular architecture"). The FKs to `products`, `customers`, `users` and
`respond_contacts` are ordinary FKs to CORE tables.

`status_id` points at the status engine (entity `sample_request`), so labels are editable
in Status graphs; the legal moves live in `sample_request_service`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


DELIVERY_MODES = ("driver", "self_deliver", "self_collect")
LOAN_UNITS = ("days", "months")
GOODS_FROM = ("demo", "new", "either")
SOURCES = ("staff", "portal")


class SampleRequest(Base, CompanyScopedMixin):
    """One consignment requisition (`SR-000001`)."""

    __tablename__ = "sample_requests"
    __audit_entity_type__ = "project_sample_requests"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    #: NOT NULL here although the mixin leaves it nullable: the insert stamp fills it, and the
    #: numbering lock is keyed on it.
    company_id = Column(UUID(as_uuid=False), ForeignKey("companies.id"), nullable=False, index=True)
    #: `SR-000001`, a per-company series copied from `StockTransfer.transfer_no`.
    request_no = Column(String(20), nullable=False)
    status_id = Column(
        UUID(as_uuid=False), ForeignKey("statuses.id", ondelete="SET NULL"), nullable=True
    )

    project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True
    )
    #: A typed title for a project that is not registered (the soft transition, card rule 2).
    project_title = Column(String(255), nullable=True)
    customer_id = Column(
        UUID(as_uuid=False), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    #: Typed when the customer has no debtor code (card rule 4); CRM never creates the debtor.
    customer_name = Column(String(255), nullable=True)
    salesperson_id = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    source = Column(String(10), nullable=False, server_default="staff")
    #: The portal owner. No FK to the portal space: it is a token-scoped string.
    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True
    )
    space_id = Column(Text, nullable=True)

    request_date = Column(Date, nullable=False, server_default=func.current_date())
    delivery_date = Column(Date, nullable=True)
    scope_note = Column(String(255), nullable=True)
    ship_to_name = Column(String(255), nullable=True)
    ship_to_address = Column(Text, nullable=True)
    site_contact_name = Column(String(255), nullable=True)
    site_contact_phone = Column(String(60), nullable=True)
    site_contact_note = Column(String(255), nullable=True)
    delivery_mode = Column(String(20), nullable=True)
    loan_value = Column(Integer, nullable=False, server_default="2")
    loan_unit = Column(String(10), nullable=False, server_default="months")
    goods_from = Column(String(10), nullable=False, server_default="either")
    purpose = Column(Text, nullable=True)
    board_mounting = Column(Boolean, nullable=False, server_default="false")
    board_label = Column(String(255), nullable=True)

    #: Coordinator-only fields.
    consignment_no = Column(String(80), nullable=True)
    po_no = Column(String(80), nullable=True)

    submitted_at = Column(DateTime(timezone=False), nullable=True)
    confirmed_at = Column(Date, nullable=True)
    delivered_at = Column(Date, nullable=True)
    return_due = Column(Date, nullable=True)
    cancelled_at = Column(DateTime(timezone=False), nullable=True)
    cancel_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    lines = relationship(
        "SampleRequestLine",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="SampleRequestLine.line_no",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "request_no", name="uq_sample_request_no"),
        # No project_id-or-title CHECK: a draft may be saved with neither (every field is
        # optional on a draft, contract); the rule is enforced when the request is submitted.
        CheckConstraint(
            "delivery_mode IS NULL OR delivery_mode IN ('driver','self_deliver','self_collect')",
            name="ck_sample_requests_delivery_mode",
        ),
        CheckConstraint("loan_unit IN ('days','months')", name="ck_sample_requests_loan_unit"),
        CheckConstraint(
            "goods_from IN ('demo','new','either')", name="ck_sample_requests_goods_from"
        ),
        CheckConstraint("source IN ('staff','portal')", name="ck_sample_requests_source"),
        CheckConstraint("loan_value >= 1", name="ck_sample_requests_loan_value"),
        Index("ix_sample_requests_status", "status_id"),
        Index("ix_sample_requests_project", "project_id"),
        Index("ix_sample_requests_salesperson", "salesperson_id"),
        Index("ix_sample_requests_contact", "contact_id"),
        {"schema": "projects"},
    )


class SampleRequestLine(Base):
    """One product line. The board is a header flag, never a line (card rule 6)."""

    __tablename__ = "sample_request_lines"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    request_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.sample_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_no = Column(Integer, nullable=False)
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    qty = Column(Integer, nullable=False, server_default="1")
    remark = Column(String(255), nullable=True)

    request = relationship("SampleRequest", back_populates="lines")

    __table_args__ = (
        CheckConstraint("qty >= 1", name="ck_sample_request_lines_qty"),
        Index("ix_sample_request_lines_request", "request_id"),
        {"schema": "projects"},
    )


REQUEST_NO_PREFIX = "SR-"
REQUEST_NO_DIGITS = 6


def next_request_no(bind, company_id) -> str:
    """Highest issued number for the company plus one (never the count: a cancelled
    request's number is on paperwork and must not be reissued). Same discipline, and the
    discipline as `app.models.stock_transfer.next_transfer_no`, plus a per-company lock."""
    table = SampleRequest.__table__
    # Two concurrent drafts of one company would read the same max; the transaction-scoped
    # lock makes the second wait for the first's commit.
    bind.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"sample_request:{company_id}"},
    )
    latest = bind.execute(
        select(table.c.request_no)
        .where(
            table.c.company_id == company_id,
            table.c.request_no.like(f"{REQUEST_NO_PREFIX}%"),
        )
        .order_by(func.length(table.c.request_no).desc(), table.c.request_no.desc())
        .limit(1)
    ).scalar()
    tail = (latest or "")[len(REQUEST_NO_PREFIX):]
    highest = int(tail) if tail.isdigit() else 0
    return f"{REQUEST_NO_PREFIX}{highest + 1:0{REQUEST_NO_DIGITS}d}"


@event.listens_for(SampleRequest, "before_insert")
def _stamp_request_no(_mapper, connection, target) -> None:
    if getattr(target, "request_no", None):
        return
    target.request_no = next_request_no(connection, target.company_id)
