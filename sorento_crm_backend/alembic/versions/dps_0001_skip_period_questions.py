"""DO-PERIOD-SKIP: per-contact "Skip date-range questions" switch.

`respond_contacts.chatbot_skip_period_questions` BOOLEAN NOT NULL DEFAULT false. Additive and
re-runnable; nobody is pre-set (the owner sets it per contact).

Revision ID: dps_0001_skip_period_questions
Revises: wa_trim_0001_stock_compact
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = "dps_0001_skip_period_questions"
down_revision = "wa_trim_0001_stock_compact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(
        sa.text(
            "ALTER TABLE respond_contacts ADD COLUMN IF NOT EXISTS chatbot_skip_period_questions "
            "BOOLEAN NOT NULL DEFAULT false"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("ALTER TABLE respond_contacts DROP COLUMN IF EXISTS chatbot_skip_period_questions")
    )
