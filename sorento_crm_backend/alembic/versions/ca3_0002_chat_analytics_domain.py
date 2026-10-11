"""Seed the `chat_analytics` routing domain (CA-3, chat analytics in the bot).

`documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md`, slice 4. One
`chatbot_domains` row: the intent `chat_report`, the one tool `crm_report_ask` (the same tool the
sales ranking uses, now over `dataset=chat`), reveal key `chatbot.analytics`. Nobody holds the
reveal after this migration (owner ruling Q4 a, 10 Oct 2026): the owner grants it per contact on
the field reveals screen ("Chat analytics").

Additive and idempotent: inserted only if absent, so an owner edit made before a re-run is never
overwritten. `apply(bind)` is shared with `scripts.bootstrap_env`, as the sales domain's is.

Revision ID: ca3_0002_chat_analytics_domain
Revises: ca3_0001_chat_report_prompt
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ca3_0002_chat_analytics_domain"
down_revision = "ca3_0001_chat_report_prompt"
branch_labels = None
depends_on = None

CHAT_ANALYTICS_DOMAIN = {
    "name": "chat_analytics",
    "label": "chat analytics",
    "intents": ["chat_report"],
    "tools": ["crm_report_ask"],
    "escalation_team_code": "customer_service",
    "switch_words": [],
    "reveal_key": "chatbot.analytics",
}


def apply(bind) -> None:
    row = CHAT_ANALYTICS_DOMAIN
    bind.execute(
        sa.text(
            "INSERT INTO chatbot_domains (id, name, label, intents, tools, primary_tool, "
            "escalation_team_code, switch_words, narrowing, takes_date_filter, reveal_key, "
            "supported, ladder, sort_order) "
            "SELECT gen_random_uuid(), :name, :label, CAST(:intents AS text[]), "
            "CAST(:tools AS text[]), NULL, :team, CAST(:words AS text[]), "
            "'{}'::jsonb, false, :reveal, true, '{}', "
            "COALESCE((SELECT max(sort_order) + 1 FROM chatbot_domains), 0) "
            "WHERE NOT EXISTS (SELECT 1 FROM chatbot_domains WHERE name = :name)"
        ),
        {
            "name": row["name"],
            "label": row["label"],
            "intents": row["intents"],
            "tools": row["tools"],
            "team": row["escalation_team_code"],
            "words": row["switch_words"],
            "reveal": row["reveal_key"],
        },
    )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM chatbot_domains WHERE name = 'chat_analytics'"))
