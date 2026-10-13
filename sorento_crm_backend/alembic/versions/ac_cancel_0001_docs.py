"""AutoCount Cancel SO / Cancel PO (AC-CANCEL-CRM, contract 2.8).

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md.

1. `autocount_cancel_docs` / `autocount_cancel_lines`: the CRM's copy of AutoCount XS / XP
   documents, keyed (company, kind, book, DocKey) and (doc, DtlKey); each line carries the SO /
   PO / SPO line it was matched to, or the reason it was not.
2. `qty_cancelled` on `sales_order_lines`, `purchase_order_lines` and `spo_allocations`: derived
   from (1) on every sync; AutoCount's own ordered and TransferedQty columns are untouched.

Additive only: new tables, and NOT NULL columns with a server default of 0, so no existing row
changes meaning. Downgrade drops everything this adds.

Revision ID: ac_cancel_0001_docs
Revises: mig_idx_chunks_document_id
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ac_cancel_0001_docs"
down_revision = "mig_idx_chunks_document_id"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=False)
_JSON = postgresql.JSONB(astext_type=sa.Text())
_QTY_TABLES = ("sales_order_lines", "purchase_order_lines", "spo_allocations")


def upgrade() -> None:
    op.create_table(
        "autocount_cancel_docs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("company_id", _UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_book", sa.String(20), nullable=False),
        sa.Column("doc_key", sa.BigInteger(), nullable=False),
        sa.Column("doc_no", sa.String(50), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column("is_void", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_vanished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_record", _JSON, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_autocount_cancel_docs_company_id", "autocount_cancel_docs", ["company_id"])
    op.create_index(
        "uq_autocount_cancel_docs_identity",
        "autocount_cancel_docs",
        ["company_id", "kind", "source_book", "doc_key"],
        unique=True,
    )

    op.create_table(
        "autocount_cancel_lines",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("company_id", _UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("cancel_doc_id", _UUID,
                  sa.ForeignKey("autocount_cancel_docs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dtl_key", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("item_code", sa.String(100), nullable=True),
        sa.Column("location_code", sa.String(50), nullable=True),
        sa.Column("qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("from_doc_type", sa.String(10), nullable=True),
        sa.Column("from_doc_no", sa.String(100), nullable=True),
        sa.Column("from_dtl_key", sa.BigInteger(), nullable=True),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="waiting"),
        sa.Column("match_reason", sa.String(40), nullable=True),
        sa.Column("candidate_line_ids", _JSON, nullable=True),
        sa.Column("sales_order_line_id", _UUID,
                  sa.ForeignKey("sales_order_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("purchase_order_line_id", _UUID,
                  sa.ForeignKey("purchase_order_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("spo_allocation_id", _UUID,
                  sa.ForeignKey("spo_allocations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_autocount_cancel_lines_company_id", "autocount_cancel_lines",
                    ["company_id"])
    op.create_index("uq_autocount_cancel_lines_doc_dtl", "autocount_cancel_lines",
                    ["cancel_doc_id", "dtl_key"], unique=True)
    op.create_index("ix_autocount_cancel_lines_so_line", "autocount_cancel_lines",
                    ["sales_order_line_id"])
    op.create_index("ix_autocount_cancel_lines_po_line", "autocount_cancel_lines",
                    ["purchase_order_line_id"])
    op.create_index("ix_autocount_cancel_lines_spo", "autocount_cancel_lines",
                    ["spo_allocation_id"])
    op.create_index("ix_autocount_cancel_lines_status", "autocount_cancel_lines",
                    ["company_id", "match_status"])

    for table in _QTY_TABLES:
        op.add_column(
            table,
            sa.Column("qty_cancelled", sa.Numeric(15, 4), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for table in _QTY_TABLES:
        op.drop_column(table, "qty_cancelled")
    op.drop_table("autocount_cancel_lines")
    op.drop_table("autocount_cancel_docs")
