"""AutoCount Cancel SO (XS) and Cancel PO (XP) documents, the CRM's own copy (AC-CANCEL-CRM).

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md. Fed by
`POST /external/ingest/cancel_sales_orders` and `/cancel_purchase_orders` (contract 2.8). One
header and line table pair for both kinds; `kind` says which. Consignment and consignment
return documents are stored here too, accepted and stored only (crew, 13 Oct).

A cancel line is matched to ONE SO line, PO line or SPO line, or to none with a reason
(`match_status` / `match_reason`). The target line's `qty_cancelled` is derived from these rows
on every sync (`autocount_cancel_ingest_service.recompute_cancelled`), never written by hand.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base
from app.models.base import CompanyScopedMixin

KIND_CANCEL_SO = "cancel_so"
KIND_CANCEL_PO = "cancel_po"
# Accepted and stored only (crew, 13 Oct): consignment behaviour is a later lane, so these
# documents keep their lines but are never matched and never move a quantity.
KIND_CONSIGNMENT = "consignment"
KIND_CONSIGNMENT_RETURN = "consignment_return"

MATCH_MATCHED = "matched"
MATCH_WAITING = "waiting"
MATCH_UNMATCHED = "unmatched"


class AutocountCancelDoc(Base, CompanyScopedMixin):
    __tablename__ = "autocount_cancel_docs"
    __audit_skip__ = "AutoCount sync table; the record as sent is kept in source_record"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String(20), nullable=False)
    source_book = Column(String(20), nullable=False)
    doc_key = Column(BigInteger, nullable=False)
    doc_no = Column(String(50), nullable=False)
    doc_date = Column(Date, nullable=False)
    # AutoCount's own `Cancelled` (T = DocStatus V): a voided cancel doc cancels nothing.
    is_void = Column(Boolean, nullable=False, default=False, server_default="false")
    source_modified_at = Column(DateTime(timezone=True), nullable=True)
    # Set by the deletions sweep: the doc is gone from AutoCount and cancels nothing.
    source_vanished_at = Column(DateTime(timezone=True), nullable=True)
    source_record = Column(JSONB, nullable=True)
    last_synced_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_autocount_cancel_docs_identity",
            "company_id", "kind", "source_book", "doc_key",
            unique=True,
        ),
    )


class AutocountCancelLine(Base, CompanyScopedMixin):
    __tablename__ = "autocount_cancel_lines"
    __audit_skip__ = "AutoCount sync line table; rewritten by every push of its document"
    __audit_parent__ = "cancel_doc_id"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    cancel_doc_id = Column(
        UUID(as_uuid=False), ForeignKey("autocount_cancel_docs.id", ondelete="CASCADE"),
        nullable=False,
    )
    dtl_key = Column(BigInteger, nullable=False)
    seq = Column(Integer, nullable=True)
    item_code = Column(String(100), nullable=True)
    location_code = Column(String(50), nullable=True)
    qty = Column(Numeric(15, 4), nullable=False, default=0)
    from_doc_type = Column(String(10), nullable=True)
    from_doc_no = Column(String(100), nullable=True)
    from_dtl_key = Column(BigInteger, nullable=True)
    match_status = Column(String(20), nullable=False, default=MATCH_WAITING)
    match_reason = Column(String(40), nullable=True)
    # The candidate line ids the match was chosen from (evidence for an ambiguous or unmatched line).
    candidate_line_ids = Column(JSONB, nullable=True)
    sales_order_line_id = Column(
        UUID(as_uuid=False), ForeignKey("sales_order_lines.id", ondelete="SET NULL"), nullable=True
    )
    purchase_order_line_id = Column(
        UUID(as_uuid=False), ForeignKey("purchase_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    spo_allocation_id = Column(
        UUID(as_uuid=False), ForeignKey("spo_allocations.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_autocount_cancel_lines_doc_dtl", "cancel_doc_id", "dtl_key", unique=True),
        Index("ix_autocount_cancel_lines_so_line", "sales_order_line_id"),
        Index("ix_autocount_cancel_lines_po_line", "purchase_order_line_id"),
        Index("ix_autocount_cancel_lines_spo", "spo_allocation_id"),
        Index("ix_autocount_cancel_lines_status", "company_id", "match_status"),
    )
