"""Parser v2 (PV2) schema: the flag columns, attachment-type aliases, alias precedence and
the unknown-words log (CONTRACT-PV2-13oct.md section 6).

Additive only. `chatbot_parser_mode` defaults to 'v1' everywhere, so no turn changes path
until a contact or a dev copy is set to 'v2'. Idempotent (IF NOT EXISTS) because the shared
dev database converges through `create_all` as well as alembic.

Revision ID: pv2_0001_schema
Revises: spec_in_0001_stage_label
"""
from alembic import op


revision = "pv2_0001_schema"
down_revision = "spec_in_0001_stage_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE respond_contacts ADD COLUMN IF NOT EXISTS chatbot_parser_mode TEXT NULL")
    op.execute(
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS chatbot_parser_mode TEXT NOT NULL "
        "DEFAULT 'v1'"
    )
    op.execute(
        "ALTER TABLE attachment_types ADD COLUMN IF NOT EXISTS search_synonyms JSONB NOT NULL "
        "DEFAULT '[]'::jsonb"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_alias_precedence (
            id UUID PRIMARY KEY,
            kind TEXT NOT NULL,
            alias_norm TEXT NOT NULL,
            winner_key TEXT NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_chatbot_alias_precedence UNIQUE (kind, alias_norm)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_unrecognised_phrases (
            id UUID PRIMARY KEY,
            phrase TEXT NOT NULL,
            kind TEXT NOT NULL,
            domain TEXT NULL,
            contact_id TEXT NULL,
            turn_id TEXT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            first_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_chatbot_unrecognised_phrases UNIQUE (phrase, kind)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chatbot_unrecognised_phrases")
    op.execute("DROP TABLE IF EXISTS chatbot_alias_precedence")
    op.execute("ALTER TABLE attachment_types DROP COLUMN IF EXISTS search_synonyms")
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS chatbot_parser_mode")
    op.execute("ALTER TABLE respond_contacts DROP COLUMN IF EXISTS chatbot_parser_mode")
