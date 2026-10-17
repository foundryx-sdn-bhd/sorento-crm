"""Finance module models: billing documents from AutoCount (plan 3.1, 3.2; slice S0, #1309).

**Every table here lives in the `finance` Postgres schema** (the owner's ask; the ADR-0011
precedent the `sales` module followed): the schema is the module key, so there is no
`finance_` prefix inside it. Foreign keys between module tables are schema-qualified
(`finance.billing_documents.id`); foreign keys to core (`companies`, `customers`,
`sales_agents`, `products`, `sales_order_lines`) stay unqualified and resolve in `public`.

One typed table holds the four AutoCount Sales-module billing documents (invoice, cash sale,
credit note, debit note): same shape, told apart by `document_type`, a CHECK rather than an
ENUM or a lookup (ADR 0013). Amounts are stored as AutoCount prints them, positive on a
credit note; the sign is applied only where the invoiced basis sums them (S1).

The only writer is `app/services/finance/billing_document_ingest_service.py`.
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import CompanyScopedMixin
from app.services.scm.demand_class import check_constraint_sql

SCHEMA = "finance"

INVOICE = "invoice"
CASH_SALE = "cash_sale"
CREDIT_NOTE = "credit_note"
DEBIT_NOTE = "debit_note"
DOCUMENT_TYPES = (INVOICE, CASH_SALE, CREDIT_NOTE, DEBIT_NOTE)

POSTED = "posted"
CANCELLED = "cancelled"
STATUSES = (POSTED, CANCELLED)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid_str() -> str:
    return str(uuid.uuid4())


class BillingDocument(CompanyScopedMixin, Base):
    """One AutoCount billing document header (plan 3.2).

    Masters that do not resolve land NULL with the code kept (`debtor_code`, `agent_code`):
    a billing document is money, so it is never held back for a missing master.
    `status` is AutoCount's Cancelled flag, not a payment status; a cancelled document stays
    visible and out of every total.
    """

    __tablename__ = "billing_documents"
    __audit_track__ = True
    # `audit_log.entity_type` defaults to the bare table name; bare names can collide across
    # schemas (the `sales.teams` reason), so this one is qualified.
    __audit_entity_type__ = "finance_billing_documents"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    document_type = Column(String(20), nullable=False)
    # AutoCount DocNo: display and search. Not unique (AutoCount can renumber).
    doc_no = Column(String(50), nullable=False)
    # The basis date. No floor (ruling Q2): the shared service owns the start date.
    doc_date = Column(Date, nullable=False)
    customer_id = Column(
        UUID(as_uuid=False), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    # As sent, kept when unresolved (the `sales_orders.debtor_code` rule).
    debtor_code = Column(String(64), nullable=True)
    customer_name = Column(String(255), nullable=True)
    sales_agent_id = Column(
        UUID(as_uuid=False), ForeignKey("sales_agents.id", ondelete="SET NULL"), nullable=True
    )
    agent_code = Column(String(100), nullable=True)
    currency_code = Column(String(3), nullable=False, default="MYR", server_default="MYR")
    currency_rate = Column(
        Numeric(18, 8), nullable=False, default=1, server_default=text("1")
    )
    # Document currency, as AutoCount sends them: net after discount excluding tax, tax, and
    # total (checked to equal net + tax within 0.01 at ingest).
    net_total = Column(Numeric(15, 2), nullable=True)
    tax_total = Column(Numeric(15, 2), nullable=True)
    total = Column(Numeric(15, 2), nullable=True)
    # Net in MYR as AutoCount converted it (A5); the invoiced basis sums this, so the CRM
    # never re-rounds a conversion (ruling Q12).
    local_net_total = Column(Numeric(15, 2), nullable=True)
    status = Column(String(20), nullable=False, default=POSTED, server_default=POSTED)
    # A credit or debit note's invoice (ruling Q4), resolved from `against_source_ref` or
    # `against_doc_no`; the number is kept when that invoice is not in the CRM.
    against_document_id = Column(
        UUID(as_uuid=False),
        ForeignKey(f"{SCHEMA}.billing_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    against_doc_no = Column(String(50), nullable=True)
    ref = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    # The sales order type this document was billed from (ruling Q14, S1): the DEALER /
    # PROJECT TEAM blocks of the invoiced basis. Decided at ingest by `classify_document`,
    # the ladder the SO feed shares; NULL when nothing classifies (the report's `(blank)`).
    demand_class = Column(String(32), nullable=True)
    # AutoCount's open balance (AC-BILLING S5, contract 2.10), written only by the
    # `ar_outstanding` push. NULL is "not synced or not posted to GL" and is shown blank,
    # never 0; a credit note's unapplied amount is negative.
    outstanding = Column(Numeric(15, 2), nullable=True)
    due_date = Column(Date, nullable=True)
    outstanding_as_of = Column(DateTime(timezone=False), nullable=True)
    source_system = Column(
        String(50), nullable=False, default="autocount", server_default="autocount"
    )
    # `{database}:{IV|CS|CN|DN}:{DocKey}` (A2).
    source_ref = Column(String(255), nullable=False)
    # AutoCount LastModified (A3), naive UTC: the stale guard.
    source_modified_at = Column(DateTime(timezone=False), nullable=True)
    last_synced_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    lines = relationship(
        "BillingDocumentLine",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="BillingDocumentLine.line_no",
    )

    __table_args__ = (
        CheckConstraint(
            _in_list("document_type", DOCUMENT_TYPES), name="ck_finance_billing_documents_type"
        ),
        CheckConstraint(_in_list("status", STATUSES), name="ck_finance_billing_documents_status"),
        CheckConstraint(
            check_constraint_sql(), name="ck_finance_billing_documents_demand_class"
        ),
        # The database backstop behind the `integration_references` key: a second writer
        # cannot land the same document twice (UAC S0-7: the type is part of the key).
        Index(
            "uq_finance_billing_documents_company_type_ref",
            "company_id",
            "document_type",
            "source_ref",
            unique=True,
        ),
        Index("ix_finance_billing_documents_company_type_doc_no", "company_id", "document_type", "doc_no"),
        Index("ix_finance_billing_documents_company_date", "company_id", "doc_date"),
        # The S1 team join: a document counts for its own agent on its own date.
        Index(
            "ix_finance_billing_documents_company_agent_date",
            "company_id",
            "sales_agent_id",
            "doc_date",
        ),
        Index("ix_finance_billing_documents_company_customer", "company_id", "customer_id"),
        Index("ix_finance_billing_documents_against", "against_document_id"),
        {"schema": SCHEMA},
    )


class BillingDocumentLine(CompanyScopedMixin, Base):
    """One item line of a billing document (plan 3.2).

    Not audited: a mirror of AutoCount lines, replaced whole on every push; the header row
    and `integration_references` are the trail (plan 3.6).
    """

    __tablename__ = "billing_document_lines"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    document_id = Column(
        UUID(as_uuid=False),
        ForeignKey(f"{SCHEMA}.billing_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_no = Column(Integer, nullable=True)
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    # As sent, kept when unresolved.
    item_code = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    uom = Column(String(20), nullable=True)
    quantity = Column(Numeric(15, 4), nullable=True)
    unit_price = Column(Numeric(15, 4), nullable=True)
    discount_amount = Column(Numeric(15, 2), nullable=True)
    # Excluding tax, after discount.
    net_amount = Column(Numeric(15, 2), nullable=True)
    tax_code = Column(String(20), nullable=True)
    tax_rate = Column(Numeric(7, 4), nullable=True)
    tax_amount = Column(Numeric(15, 2), nullable=True)
    # Including tax.
    line_total = Column(Numeric(15, 2), nullable=True)
    sales_order_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sales_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    # As sent: the SO or DO line this one was transferred from (A6). No DO FK (plan 3.2).
    from_doc_type = Column(String(10), nullable=True)
    from_doc_no = Column(String(50), nullable=True)
    from_line_ref = Column(String(255), nullable=True)
    # AutoCount DtlKey.
    source_ref = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document = relationship("BillingDocument", back_populates="lines")

    __table_args__ = (
        Index(
            "uq_finance_billing_document_lines_document_ref",
            "document_id",
            "source_ref",
            unique=True,
        ),
        Index("ix_finance_billing_document_lines_sales_order_line", "sales_order_line_id"),
        Index("ix_finance_billing_document_lines_product", "product_id"),
        {"schema": SCHEMA},
    )
