"""QUOTED-REPLY - chat_histories.chatbot_turn_id, the engine turn a bot reply came from.

Owner ruling, 7 Oct 2026: a quoted bot message is the context of the dealer's reply, so
the outgoing row must say which engine turn produced it. Nullable, no index: the lookup
goes through the existing (contact_id, message_id) index first.

Revision ID: qr_0001_chat_histories_turn_link
Revises: aig_0001_chatbot_allowed
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "qr_0001_chat_histories_turn_link"
down_revision = "aig_0001_chatbot_allowed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(
        sa.text("ALTER TABLE chat_histories ADD COLUMN IF NOT EXISTS chatbot_turn_id VARCHAR(36) NULL")
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chat_histories DROP COLUMN IF EXISTS chatbot_turn_id")
