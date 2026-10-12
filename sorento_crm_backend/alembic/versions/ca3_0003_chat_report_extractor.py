"""Publish the `chat_report_extractor` prompt (CA-3, focused extractor, 10 Oct 2026).

`documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md`, "Revision 10 Oct
2026: focused extractor". The registry seed gives every key in `PROMPT_KEYS` a version 1 from its
code fallback and a `production` label, only when the key has none; an owner edit is never
overwritten.

Revision ID: ca3_0003_chat_report_extractor
Revises: ca3_0002_chat_analytics_domain
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ca3_0003_chat_report_extractor"
down_revision = "ca3_0002_chat_analytics_domain"
branch_labels = None
depends_on = None

PROMPT_NAME = "chat_report_extractor"


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    seed_prompt_registry(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    params = {"n": PROMPT_NAME}
    bind.execute(sa.text("DELETE FROM ai_prompt_labels WHERE name = :n"), params)
    bind.execute(sa.text("DELETE FROM ai_prompt_versions WHERE name = :n"), params)
