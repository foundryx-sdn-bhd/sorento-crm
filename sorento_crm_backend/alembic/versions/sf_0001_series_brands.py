"""A series may carry many brands; none means every brand (SERIES-FILTERS).

Plan: documentation/plans/project-sales/PLAN-series-filters-15oct.md.

Adds `projects.series_brands` and backfills it from `projects.series.brand_id`, so a series that
showed one brand before shows the same brand after. `brand_id` stays, no longer read or written,
until a later cleanup. Additive only; downgrade drops the table.

Revision ID: sf_0001_series_brands
Revises: r2g_0001_series_sets_uuid_id
Create Date: 2026-10-15
"""
from alembic import op

revision = "sf_0001_series_brands"
down_revision = "r2g_0001_series_sets_uuid_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projects.series_brands (
            series_id uuid NOT NULL REFERENCES projects.series (id) ON DELETE CASCADE,
            brand_id  uuid NOT NULL REFERENCES public.brands (id) ON DELETE CASCADE,
            PRIMARY KEY (series_id, brand_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO projects.series_brands (series_id, brand_id)
        SELECT s.id, s.brand_id
        FROM projects.series s
        WHERE s.brand_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS projects.series_brands")
