"""AutoCount Consignment on the CRM (AC-CONSIGN-CRM).

Plan: documentation/plans/autocount/PLAN-autocount-consign-crm-14oct.md.

1. `sales_orders.doc_type`: `sales_order` (every existing row, by the server default) or
   `consignment` (an AutoCount CG document the consignments ingest writes as demand, owner
   ruling 1, 13 Oct). Checked, so a typo cannot become a third kind nothing reads.
2. `sales_order_lines.qty_returned`: what live AutoCount Consignment Returns matched to a
   consignment line add up to, capped at ordered, derived on every sync (ruling 2: reporting
   only, no planned / delivered / status change).

Additive only: NOT NULL columns with server defaults, so no existing row changes meaning.
Downgrade drops both.

Revision ID: ac_consign_0001_doc_type
Revises: ac_cancel_0001_docs
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op

revision = "ac_consign_0001_doc_type"
down_revision = "ac_cancel_0001_docs"
branch_labels = None
depends_on = None

_CHECK = "ck_sales_orders_doc_type"


def upgrade() -> None:
    op.add_column(
        "sales_orders",
        sa.Column("doc_type", sa.String(20), nullable=False, server_default="sales_order"),
    )
    op.create_check_constraint(
        _CHECK, "sales_orders", "doc_type IN ('sales_order', 'consignment')"
    )
    op.add_column(
        "sales_order_lines",
        sa.Column("qty_returned", sa.Numeric(15, 4), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("sales_order_lines", "qty_returned")
    op.drop_constraint(_CHECK, "sales_orders", type_="check")
    op.drop_column("sales_orders", "doc_type")
