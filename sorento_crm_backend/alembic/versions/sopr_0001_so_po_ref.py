"""SO-PO-REF: the customer's PO number on a sales order.

`sales_orders.po_ref` holds the PO number as AutoCount printed it (UDFNote via the
shared-service mapping), `po_ref_key` its folded form (`app.services.scm.po_ref`) the
chatbot matches on. Additive only; no backfill (owner ruling 7 Oct 2026, the raw value was
never stored, open orders are re-pushed from shared service).

Revision ID: sopr_0001_so_po_ref
Revises: aig_0001_chatbot_allowed
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "sopr_0001_so_po_ref"
down_revision = "aig_0001_chatbot_allowed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS po_ref VARCHAR(255)"))
    bind.execute(sa.text("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS po_ref_key VARCHAR(255)"))
    bind.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_sales_orders_po_ref_key ON sales_orders (po_ref_key) "
            "WHERE po_ref_key IS NOT NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP INDEX IF EXISTS ix_sales_orders_po_ref_key"))
    bind.execute(sa.text("ALTER TABLE sales_orders DROP COLUMN IF EXISTS po_ref_key"))
    bind.execute(sa.text("ALTER TABLE sales_orders DROP COLUMN IF EXISTS po_ref"))
