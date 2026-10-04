"""Every chatbot contact gets the COMPACT stock view (WA-MSG-TRIM, owner 3 Oct 2026).

Since 1 Oct 2026 Meta charges every outgoing WhatsApp message, and the detailed view (one
row per location) is what pushes a stock answer past the chunk limit into extra messages.
Every ``stock_visibility_policies`` row in ``detailed`` becomes ``compact``: the global
default (seeded ``detailed`` by 416), access-type rows and contact overrides alike, so no
tier can still hand a contact the detailed view. ``availability`` is the separate dealer
mode and is never touched.

Pure data, no schema change on the policy table. The ids it flipped are kept in
``stock_visibility_wa_trim_backup`` so the downgrade restores exactly those rows, and not
a row an admin set to ``compact`` on purpose (a flipped row an admin later re-saves as
``compact`` still goes back, the backup cannot tell the two apart). The backup table has
no model; ``alembic/env.py`` ``MIGRATION_ONLY_TABLES`` keeps autogenerate from proposing
to drop it. Idempotent: a second run finds no
``detailed`` row and the backup insert skips ids it already holds.

Plan: ``documentation/plans/chatbot/PLAN-wa-msg-trim.md``.

Revision ID: wa_trim_0001_stock_compact
Revises: sa_person_label_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "wa_trim_0001_stock_compact"
down_revision = "sa_person_label_0001"
branch_labels = None
depends_on = None

TABLE = "stock_visibility_policies"
BACKUP = "stock_visibility_wa_trim_backup"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(f"CREATE TABLE IF NOT EXISTS {BACKUP} (policy_id uuid PRIMARY KEY)"))
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {BACKUP} (policy_id)
            SELECT id FROM {TABLE} WHERE mode = 'detailed'
            ON CONFLICT (policy_id) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            f"UPDATE {TABLE} SET mode = 'compact', updated_at = now() WHERE mode = 'detailed'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT to_regclass('{BACKUP}')")).scalar() is None:
        return
    bind.execute(
        sa.text(
            f"""
            UPDATE {TABLE} SET mode = 'detailed', updated_at = now()
            WHERE mode = 'compact' AND id IN (SELECT policy_id FROM {BACKUP})
            """
        )
    )
    bind.execute(sa.text(f"DROP TABLE {BACKUP}"))
