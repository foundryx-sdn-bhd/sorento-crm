"""AI-ALLOWED-GATE - the per-contact "AI replies allowed" flag.

Owner ask, 5 Oct 2026: the CRM decides which contacts the AI answers; a contact that is
blocked, or not in the CRM at all, gets nothing. Owner ruling, 5 Oct 2026: every contact
that exists now keeps answering, every NEW contact starts blocked until a human ticks it.

The default is set in two steps on purpose. ADD COLUMN ... DEFAULT true backfills every
existing row TRUE (prod behaviour unchanged on release); SET DEFAULT false then applies to
new rows only. A second run finds the column present, skips the ADD, and re-applies the
same default, so it never re-allows a row somebody blocked. The downgrade drops the column.

Revision ID: aig_0001_chatbot_allowed
Revises: dps_0001_skip_period_questions
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "aig_0001_chatbot_allowed"
down_revision = "dps_0001_skip_period_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # ADD COLUMN takes an ACCESS EXCLUSIVE lock; give up rather than queue every
    # chatbot turn behind a long transaction on respond_contacts.
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(
        sa.text(
            "ALTER TABLE respond_contacts ADD COLUMN IF NOT EXISTS chatbot_allowed "
            "BOOLEAN NOT NULL DEFAULT true"
        )
    )
    # New rows start blocked; existing rows were backfilled TRUE by the ADD above.
    bind.execute(sa.text("ALTER TABLE respond_contacts ALTER COLUMN chatbot_allowed SET DEFAULT false"))


def downgrade() -> None:
    op.get_bind().execute(sa.text("ALTER TABLE respond_contacts DROP COLUMN IF EXISTS chatbot_allowed"))
