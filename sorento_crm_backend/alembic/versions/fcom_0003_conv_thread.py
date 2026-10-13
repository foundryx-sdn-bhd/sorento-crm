"""Form comments: reply, edit and delete columns on entity_conversation_messages

Revision ID: fcom_0003_conv_thread
Revises: fcom_0002_conv_check_widen
Create Date: 2026-10-09 09:00:00.000000

`PLAN-form-comments-09oct.md` (owner hand test round 2). Additive: parent_id (one-level reply,
self FK, cascades), edited_at and deleted_at (a deleted comment with replies stays as a
placeholder).
"""
import sqlalchemy as sa
from alembic import op

revision = "fcom_0003_conv_thread"
down_revision = "fcom_0002_conv_check_widen"
branch_labels = None
depends_on = None

TABLE = "entity_conversation_messages"
FK = "fk_entity_conversation_messages_parent_id"
IX = "ix_entity_conversation_messages_parent_id"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("parent_id", sa.String(36), nullable=True))
    op.add_column(TABLE, sa.Column("edited_at", sa.DateTime(timezone=False), nullable=True))
    op.add_column(TABLE, sa.Column("deleted_at", sa.DateTime(timezone=False), nullable=True))
    op.create_foreign_key(FK, TABLE, TABLE, ["parent_id"], ["id"], ondelete="CASCADE")
    op.create_index(IX, TABLE, ["parent_id"])


def downgrade() -> None:
    op.drop_index(IX, table_name=TABLE)
    op.drop_constraint(FK, TABLE, type_="foreignkey")
    op.drop_column(TABLE, "deleted_at")
    op.drop_column(TABLE, "edited_at")
    op.drop_column(TABLE, "parent_id")
