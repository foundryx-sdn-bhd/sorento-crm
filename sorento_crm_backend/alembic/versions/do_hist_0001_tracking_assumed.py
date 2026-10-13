"""orders.tracking_assumed_at: a DO the history backfill assumed delivered (DO-HISTORY).

Set by `scripts/do_history_assume_delivered.py` on a pre-cutover AutoCount DO that has no Order
Tracking, together with the delivered status and `actual_delivery_date` = DocDate (owner Q2 a,
13 Oct 2026); cleared by a later Order Tracking row for that DO. It is also the revert key.

Additive and nullable, no default, nothing backfilled.

Revision ID: do_hist_0001_tracking_assumed
Revises: maskp_0002_measure_words
"""
from __future__ import annotations

from alembic import op

revision = "do_hist_0001_tracking_assumed"
down_revision = "maskp_0002_measure_words"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unqualified: `orders` exists only in the default schema, and the migration test points
    # search_path at a scratch copy.
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_assumed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS tracking_assumed_at")
