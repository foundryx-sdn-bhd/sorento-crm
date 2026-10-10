"""Chat analytics CA-2: the topic audit table.

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Topic audit".

`chatbot.chat_label_audits` holds one row per ask a person is asked to judge, per taxonomy
version: the draw writes the row, the reviewer's verdict fills it in. The ask cascades, so
a pruned fact takes its audit row with it, and a corrected topic must be a taxonomy code.
There is NO column for what the customer wrote: the screen reads the message from the turn
envelope at request time and never stores it. `reviewed_by` is the reviewer's user id as
text, with no foreign key, like the other analytic rows that name a person.

Additive and idempotent: `if_not_exists` on the table.

Revision ID: chat_topics_0002_audit
Revises: chat_topics_0001_taxonomy
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "chat_topics_0002_audit"
down_revision = "chat_topics_0001_taxonomy"
branch_labels = None
depends_on = None

_SCHEMA = "chatbot"


def upgrade() -> None:
    op.create_table(
        "chat_label_audits",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "ask_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(f"{_SCHEMA}.chat_ask_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("taxonomy_version", sa.SmallInteger(), nullable=False),
        sa.Column("drawn_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # right | wrong_topic | wrong_subject
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column(
            "corrected_topic",
            sa.Text(),
            sa.ForeignKey(f"{_SCHEMA}.chat_topics.code"),
            nullable=True,
        ),
        # right | wrong
        sa.Column("products_verdict", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("ask_id", "taxonomy_version", name="uq_chat_label_audits_ask_version"),
        schema=_SCHEMA,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("chat_label_audits", schema=_SCHEMA, if_exists=True)
