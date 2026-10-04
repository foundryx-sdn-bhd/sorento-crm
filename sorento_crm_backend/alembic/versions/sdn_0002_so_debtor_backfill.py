"""Backfill `sales_orders.debtor_name` from the customer's current name.

DEBTOR-CODE-LINK (crew, 4 Oct 2026): `sdn_0001_so_debtor_name` added the column but left every
existing order NULL (152k on dev), so the SO screens fell back to the live master name and a
renamed customer rewrote the name on its old orders. This freezes today's master name onto every
order that carries none. Additive: a stored name is never overwritten, an order without a
customer stays NULL, and a second run changes nothing.

Downgrade is a no-op: a backfilled name cannot be told apart from one AutoCount sent.

Revision ID: sdn_0002_so_debtor_backfill
Revises: dev_login_0001
Create Date: 2026-10-04
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "sdn_0002_so_debtor_backfill"
down_revision = "dev_login_0001"
branch_labels = None
depends_on = None


def backfill_so_debtor_name(bind) -> int:
    result = bind.execute(
        text(
            "UPDATE sales_orders so SET debtor_name = btrim(c.customer_name) "
            "FROM customers c "
            "WHERE so.customer_id = c.id AND so.debtor_name IS NULL "
            "AND btrim(c.customer_name) <> ''"
        )
    )
    return result.rowcount


def upgrade() -> None:
    backfill_so_debtor_name(op.get_bind())


def downgrade() -> None:
    pass
