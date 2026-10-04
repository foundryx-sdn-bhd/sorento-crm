"""Order inquiry row keeps the item code it had before a product change ("was X").

Revision ID: oipf_0001_prev_item_code
Revises: merge_03oct_join5
Create Date: 2026-10-02

`documentation/plans/scm/PLAN-oi-product-follow-2oct.md` R1: when AutoCount swaps the
product on an SO line, the planning board's Confirm restates the OI row on the new code
and keeps the old one beside it, the same way `previous_qty` / `previous_delivery_date`
keep a quantity or date change. One nullable column, no backfill (the owner's one-off
correction script fills it for rows already wrong on prod).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "oipf_0001_prev_item_code"
down_revision = "merge_03oct_join5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_inquiry_rows",
        sa.Column("previous_item_code", sa.String(length=120), nullable=True),
        schema="projects",
    )


def downgrade() -> None:
    op.drop_column("order_inquiry_rows", "previous_item_code", schema="projects")
