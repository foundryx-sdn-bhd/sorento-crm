"""MODEL-PRICES CU-6: cost views gain `cache_unknown`

Revision ID: mpr_0001_cache_unknown_views
Revises: spec_in_0001_stage_label
Create Date: 2026-10-13 00:00:00.000000

Plan: documentation/plans/analytics/PLAN-model-prices-13oct.md item 9. View-only and additive:
`analytics_connector.llm_calls` and `turn_costs` are re-created by `views.ensure_cost_views`
with `cache_unknown` appended as the last column. No data change here (the data fix is an
owner step on prod, see the plan).
"""
import sqlalchemy as sa
from alembic import op

from app.services.analytics_connector import views

revision = "mpr_0001_cache_unknown_views"
down_revision = "spec_in_0001_stage_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    views.ensure_cost_views(bind)
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": views.RO_ROLE}).scalar():
        views.grant_read_role(bind, views.RO_ROLE)


def downgrade() -> None:
    # Nothing to undo: the extra view column is additive and harmless to older code, and
    # `ensure_cost_views` re-creates both views from the code that is deployed.
    pass
