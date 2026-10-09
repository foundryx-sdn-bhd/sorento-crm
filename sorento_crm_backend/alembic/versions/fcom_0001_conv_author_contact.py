"""Form comments: a contact can author a thread message

Revision ID: fcom_0001_conv_author_contact
Revises: psl_0001_chatbot_file_links
Create Date: 2026-10-09 00:00:00.000000

`PLAN-form-comments-09oct.md` Contract. A submitter's comment on a form has no CRM user, so
the message row needs a nullable author_contact_id (respond_contacts.id). Additive only.
"""
import sqlalchemy as sa
from alembic import op

revision = "fcom_0001_conv_author_contact"
down_revision = "psl_0001_chatbot_file_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entity_conversation_messages",
        sa.Column("author_contact_id", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_entity_conv_msg_author_contact",
        "entity_conversation_messages",
        "respond_contacts",
        ["author_contact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_entity_conv_msg_author_contact", "entity_conversation_messages", type_="foreignkey")
    op.drop_column("entity_conversation_messages", "author_contact_id")
