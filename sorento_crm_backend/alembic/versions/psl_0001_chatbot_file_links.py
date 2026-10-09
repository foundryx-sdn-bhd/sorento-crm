"""PHOTO-SEND-CAP round 2: files as short links, A/B per contact.

* `chatbot_file_links`: one short link per distinct file per turn (code -> unsigned CDN
  url, contact, turn, expiry, clicks).
* `respond_contacts.chatbot_files_as_links` BOOLEAN NOT NULL DEFAULT false: every contact
  starts in attachment mode, today's behaviour (owner Q4 (a)).
* `system_settings.chatbot_file_link_expiry_days` INTEGER NULL: null = links never expire
  (owner Q1, 9 Oct 2026).

Additive and re-runnable.

Revision ID: psl_0001_chatbot_file_links
Revises: bflo_0001_borrow_order_toggle
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

revision = "psl_0001_chatbot_file_links"
down_revision = "bflo_0001_borrow_order_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS chatbot_file_links (
                id VARCHAR(36) PRIMARY KEY,
                code VARCHAR(16) NOT NULL,
                target_url TEXT NOT NULL,
                filename TEXT NULL,
                mime_type VARCHAR(255) NULL,
                contact_respond_id VARCHAR(64) NULL,
                turn_id VARCHAR(36) NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
                expires_at TIMESTAMP WITHOUT TIME ZONE NULL,
                click_count INTEGER NOT NULL DEFAULT 0,
                first_clicked_at TIMESTAMP WITHOUT TIME ZONE NULL,
                last_clicked_at TIMESTAMP WITHOUT TIME ZONE NULL
            )
            """
        )
    )
    bind.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_chatbot_file_links_code ON chatbot_file_links (code)"))
    bind.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_chatbot_file_links_contact_respond_id "
            "ON chatbot_file_links (contact_respond_id)"
        )
    )
    bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_chatbot_file_links_turn_id ON chatbot_file_links (turn_id)"))
    bind.execute(
        sa.text(
            "ALTER TABLE respond_contacts ADD COLUMN IF NOT EXISTS chatbot_files_as_links "
            "BOOLEAN NOT NULL DEFAULT false"
        )
    )
    bind.execute(
        sa.text("ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS chatbot_file_link_expiry_days INTEGER NULL")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("ALTER TABLE system_settings DROP COLUMN IF EXISTS chatbot_file_link_expiry_days"))
    bind.execute(sa.text("ALTER TABLE respond_contacts DROP COLUMN IF EXISTS chatbot_files_as_links"))
    bind.execute(sa.text("DROP TABLE IF EXISTS chatbot_file_links"))
