"""CHAT-SPEED: the direct-ingress switch and the chatbot sender target.

`system_settings.chatbot_direct_ingress_mode` (`off` | `contacts` | `all`, default `off`) and
`chatbot_direct_ingress_contacts` (respond.io contact ids, for `contacts`) decide whether the
respond.io webhook starts a turn itself. `respond_workspaces.chatbot_sender_url` and
`chatbot_sender_key_ciphertext` say where the finished actions are posted (the thin n8n
sender), beside the retry ingress pair.

Additive only and idempotent (`IF NOT EXISTS`) so the shared dev DB, which converges through
`create_all`, can take it in any order. Default `off` keeps every install exactly as it was.

Plan: documentation/plans/chatbot/PLAN-chat-speed-ingress.md.

Revision ID: chs_0001_direct_ingress
Revises: sr_0001_sample_requests
"""
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "chs_0001_direct_ingress"
down_revision = "sr_0001_sample_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS chatbot_direct_ingress_mode "
        "VARCHAR(16) NOT NULL DEFAULT 'off'"
    )
    op.execute(
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS chatbot_direct_ingress_contacts TEXT"
    )
    op.execute(
        "ALTER TABLE respond_workspaces ADD COLUMN IF NOT EXISTS chatbot_sender_url VARCHAR(512)"
    )
    op.execute(
        "ALTER TABLE respond_workspaces ADD COLUMN IF NOT EXISTS chatbot_sender_key_ciphertext TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE respond_workspaces DROP COLUMN IF EXISTS chatbot_sender_key_ciphertext")
    op.execute("ALTER TABLE respond_workspaces DROP COLUMN IF EXISTS chatbot_sender_url")
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS chatbot_direct_ingress_contacts")
    op.execute("ALTER TABLE system_settings DROP COLUMN IF EXISTS chatbot_direct_ingress_mode")
