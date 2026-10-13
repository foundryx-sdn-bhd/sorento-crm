"""orders.tracking_assumed_at: a DO the history backfill assumed delivered (DO-HISTORY).

Set by the DO ingest when an AutoCount DO dated before `DO_ASSUME_DELIVERED_BEFORE` lands with no
Order Tracking, together with the delivered status; `actual_delivery_date` stays empty (owner,
13 Oct 2026). Cleared by a later Overall Tracking row for that DO. It is also the revert key.

Additive and nullable, no default, nothing backfilled.

Revision ID: do_hist_0001_tracking_assumed
Revises: oirj_0002_theme_sorento
"""
from __future__ import annotations

from alembic import op

revision = "do_hist_0001_tracking_assumed"
down_revision = "oirj_0002_theme_sorento"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unqualified: `orders` exists only in the default schema, and the migration test points
    # search_path at a scratch copy.
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_assumed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS tracking_assumed_at")
