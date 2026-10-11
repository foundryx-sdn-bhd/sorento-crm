"""CLARIFY-POLICY S2: `chatbot_clarify_policy`, one row per question kind the chatbot asks.

Plan: documentation/plans/chatbot/PLAN-clarify-policy-11oct.md (AC-110..AC-116). The owner sets,
per kind, whether the bot asks or defaults (R1, 11 Oct 2026). `mode` must be one of the row's own
`allowed_modes` (CHECK). Seeded with today's behaviour from the ONE seed list,
`app.services.chatbot.clarify_policy.CLARIFY_POLICY_ROWS` (UAC AC-110). A later policy change
is a NEW migration against this table, never an edit to that list.

Idempotent and additive: `CREATE TABLE IF NOT EXISTS` and `INSERT ... ON CONFLICT DO NOTHING`,
so a database that converged through `create_all` (the shared dev copy) upgrades cleanly.

Revision ID: clp_0001_clarify_policy
Revises: chat_backfill_0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "clp_0001_clarify_policy"
down_revision = "chat_backfill_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.chatbot.clarify_policy import CLARIFY_POLICY_ROWS

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_clarify_policy (
            ask_kind text PRIMARY KEY,
            label text NOT NULL,
            mode text NOT NULL,
            allowed_modes text[] NOT NULL,
            default_note text NOT NULL DEFAULT '',
            repeat_limit integer NOT NULL DEFAULT 2,
            updated_at timestamp without time zone NOT NULL DEFAULT now(),
            updated_by text,
            CONSTRAINT ck_chatbot_clarify_policy_mode_allowed CHECK (mode = ANY(allowed_modes))
        )
        """
    )
    insert = sa.text(
        "INSERT INTO chatbot_clarify_policy "
        "(ask_kind, label, mode, allowed_modes, default_note, repeat_limit, updated_by) "
        "VALUES (:ask_kind, :label, :mode, :allowed_modes, :default_note, :repeat_limit, 'migration') "
        "ON CONFLICT (ask_kind) DO NOTHING"
    )
    bind = op.get_bind()
    for row in CLARIFY_POLICY_ROWS:
        bind.execute(insert, {**row, "allowed_modes": list(row["allowed_modes"])})


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chatbot_clarify_policy")
