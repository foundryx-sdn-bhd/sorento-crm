"""DO_ASSUME_DELIVERED_BEFORE moves from env to `system_settings` (DO-BACKFILL-SETTING).

One nullable DATE column read through `app/services/runtime_settings.py`: NULL falls back to the
env var of the same name, so an install that sets the env keeps working unchanged. Additive only.

Idempotent: `create_all` may already have added the column on a bootstrapped database.

Revision ID: dobs_0001_assume_delivered
Revises: sf_0001_series_brands
"""
from alembic import op

revision = "dobs_0001_assume_delivered"
down_revision = "sf_0001_series_brands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS do_assume_delivered_before DATE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS do_assume_delivered_before")
