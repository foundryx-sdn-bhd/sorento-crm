"""report engine rate limit: two System Settings instead of two constants

Lane REPORT-RATE-LIMIT-CONFIG (owner, 8 Oct 2026: "make it configurable for me"). The chatbot
report engine (`GET /api/v1/order-management/report-ask`) capped reports at 10 per contact per
10 minutes with module constants. These two columns hold the same numbers so the owner moves them
on Settings -> Chatbot media; the route reads them per request.

* ``system_settings.report_ask_rate_limit`` - reports per contact per window. NOT NULL DEFAULT 10;
  0 means no limit. Validated 0..1000 on PUT.
* ``system_settings.report_ask_rate_window_minutes`` - the window. NOT NULL DEFAULT 10. Validated
  1..1440 on PUT.

Additive and defaulted, so the downgrade is a plain drop and no data is rewritten.

Revision ID: rrl_0001_report_rate_limit
Revises: report_engine_0001_prompt
"""
from alembic import op
import sqlalchemy as sa

revision = "rrl_0001_report_rate_limit"
down_revision = "report_engine_0001_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("report_ask_rate_limit", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "system_settings",
        sa.Column(
            "report_ask_rate_window_minutes", sa.Integer(), nullable=False, server_default="10"
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "report_ask_rate_window_minutes")
    op.drop_column("system_settings", "report_ask_rate_limit")
