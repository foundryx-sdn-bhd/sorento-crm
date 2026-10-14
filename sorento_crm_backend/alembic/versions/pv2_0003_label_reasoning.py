"""Per-label reasoning effort for an agent's LLM call (owner ruling, 14 Oct 2026).

`ai_prompt_labels.reasoning_effort`: none | low | medium | high, NULL = the provider's default
(nothing is sent). Set beside `provider` / `model` from the Prompts screen. The parser v2 labels
(`chatbot_semantic_parser_v2`) start at "low".

The downgrade drops the column and its CHECK constraint.

Revision ID: pv2_0003_label_reasoning
Revises: pv2_0002_alias_seed
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "pv2_0003_label_reasoning"
down_revision = "pv2_0002_alias_seed"
branch_labels = None
depends_on = None

CHECK = "ck_ai_prompt_labels_reasoning_effort"


def upgrade() -> None:
    op.add_column("ai_prompt_labels", sa.Column("reasoning_effort", sa.String(16), nullable=True))
    op.create_check_constraint(
        CHECK, "ai_prompt_labels", "reasoning_effort IN ('none', 'low', 'medium', 'high')"
    )
    op.execute(
        "UPDATE ai_prompt_labels SET reasoning_effort = 'low' "
        "WHERE name = 'chatbot_semantic_parser_v2' AND reasoning_effort IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint(CHECK, "ai_prompt_labels", type_="check")
    op.drop_column("ai_prompt_labels", "reasoning_effort")
