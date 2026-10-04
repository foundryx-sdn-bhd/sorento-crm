"""ESCALATION-CONTROL - the per-contact "Can escalate to a person" flag.

Owner change, 30 Sep 2026: escalation is controlled PER CONTACT, not by access type.
`respond_contacts.escalation_allowed` BOOLEAN NOT NULL DEFAULT true; every existing contact
is backfilled allowed (owner ruling: no dealer-blocked backfill, blocking is only by
unticking the contact page). New contacts default allowed too. Access types get no column.

Additive and re-runnable. It also converges a copy that ran this lane's earlier SQL, where
the column was a NULLable override: a NULL there meant "inherit", which is now "allowed",
and a contact already set to false keeps false.

Revision ID: esc1_0001_escalation_allowed
Revises: oihr_0004_wide_line_table
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa

revision = "esc1_0001_escalation_allowed"
down_revision = "oihr_0004_wide_line_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Security review N3: ADD COLUMN takes an ACCESS EXCLUSIVE lock; give up rather than
    # queue every chatbot turn behind a long transaction on respond_contacts.
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(
        sa.text(
            "ALTER TABLE respond_contacts ADD COLUMN IF NOT EXISTS escalation_allowed "
            "BOOLEAN NOT NULL DEFAULT true"
        )
    )
    # A copy that ran the earlier NULLable column: NULL (inherit) reads as allowed.
    bind.execute(sa.text("UPDATE respond_contacts SET escalation_allowed = true WHERE escalation_allowed IS NULL"))
    bind.execute(sa.text("ALTER TABLE respond_contacts ALTER COLUMN escalation_allowed SET DEFAULT true"))
    bind.execute(sa.text("ALTER TABLE respond_contacts ALTER COLUMN escalation_allowed SET NOT NULL"))


def downgrade() -> None:
    op.get_bind().execute(sa.text("ALTER TABLE respond_contacts DROP COLUMN IF EXISTS escalation_allowed"))
