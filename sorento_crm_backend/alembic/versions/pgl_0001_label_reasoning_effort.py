"""ai_prompt_labels.reasoning_effort (PARSER-GEMINI-LITE).

How hard the label's model thinks before it answers, set beside the provider and model it
tunes. NULL means the provider's own default, which is what every call sent before this
column existed, so the column changes nothing until somebody sets a value. Additive and
nullable: no backfill, no lock beyond the catalog update.

Revision ID: pgl_0001_label_reasoning_effort
Revises: mig_idx_chunks_document_id
"""
from __future__ import annotations

from alembic import op

revision = "pgl_0001_label_reasoning_effort"
down_revision = "mig_idx_chunks_document_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_prompt_labels ADD COLUMN IF NOT EXISTS reasoning_effort VARCHAR(16)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_prompt_labels DROP COLUMN IF EXISTS reasoning_effort")
