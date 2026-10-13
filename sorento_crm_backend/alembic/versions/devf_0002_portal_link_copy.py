"""Seed the portal link reply lines into the prompt registry (DEV-FAILURES RC-B, owner approved
12 Oct; documentation/plans/chatbot/PLAN-dev-failures-12oct.md).

Fifteen new `chatbot_reply_portal_link*` keys (the bare line plus one per portal form, each in
en / ms / zh), seeded at v1 from their fallbacks in `chatbot_reply_copy.FALLBACK_REPLY_COPY` so
the owner can edit them in Settings > AI Prompts. `seed_prompt_registry` is insert-only and
idempotent: a key that already exists, and any edit published on it, is left alone.

Revision ID: devf_0002_portal_link_copy
Revises: devf_0001_parser_data_rules
"""
from alembic import op

from app.services.ai_prompt_seed import seed_prompt_registry

revision = "devf_0002_portal_link_copy"
down_revision = "devf_0001_parser_data_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    seed_prompt_registry(op.get_bind())


def downgrade() -> None:
    # The seeded rows stay: dropping them would strip an edit the owner may have published.
    pass
