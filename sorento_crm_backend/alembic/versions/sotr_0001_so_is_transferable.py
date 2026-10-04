"""sales_orders.is_transferable: AutoCount's SO header `Transferable` (T/F) (SO-TRANSFERABLE).

Additive and nullable, no default, nothing backfilled. NULL means AutoCount never stated it
(every existing row, and every Excel / manual / Order Inquiry order), which is treated as
transferable; only an explicit FALSE leaves the Stock Debt view (PLAN-so-transferable.md D1).

Revision ID: sotr_0001_so_is_transferable
Revises: dcm_0001_compare_mappings
"""
from __future__ import annotations

from alembic import op

revision = "sotr_0001_so_is_transferable"
down_revision = "dcm_0001_compare_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Schema-qualified: `projects.sales_orders` shares the bare name.
    op.execute("ALTER TABLE public.sales_orders ADD COLUMN IF NOT EXISTS is_transferable BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE public.sales_orders DROP COLUMN IF EXISTS is_transferable")
