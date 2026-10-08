""""Borrow from another order" behind a system setting, off by default

Revision ID: bflo_0001_borrow_order_toggle
Revises: report_drill_0001_prompt
Create Date: 2026-10-08 00:00:00.000000

`PLAN-borrow-location-only-8oct.md` D1 (owner ruling 8 Oct 2026). Sorento borrows a
location's stock, not a specific order's, and the order-back belongs on the line that
borrowed. The donor-named borrow stays behind this switch, defaulted off.
"""
import sqlalchemy as sa
from alembic import op

revision = "bflo_0001_borrow_order_toggle"
down_revision = "report_drill_0001_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "borrow_from_order_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "borrow_from_order_enabled")
