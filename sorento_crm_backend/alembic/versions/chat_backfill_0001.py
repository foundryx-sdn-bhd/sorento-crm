"""Chat analytics CA-4: facts from chat history, and the backfill run table.

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca4-backfill-09oct.md, rule B8.

- `chatbot.chat_ask_facts.turn_id` becomes nullable and `history_id bigint` is added: a
  backfilled fact names the incoming `chat_histories` row it came from instead of a turn.
  Exactly one of the two is set (CHECK), and a message has one row per ask (unique with
  `ask_seq`, as a turn has). No FK, like `turn_id`: the fact outlives its source row.
- `chatbot.chat_backfill_runs`: one row per run of one era. Counts, tokens and a cursor; no
  column for what anyone wrote.

Idempotent: the constraints are dropped if present and re-added, the column and table use
`IF NOT EXISTS`, so a database that converged through `create_all` (the shared dev copy)
upgrades cleanly. The drop and re-add is cheap at this table's size.

Revision ID: chat_backfill_0001
Revises: chat_topics_0002_audit
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "chat_backfill_0001"
down_revision = "chat_topics_0002_audit"
branch_labels = None
depends_on = None

_SCHEMA = "chatbot"


def _schema(bind) -> str:
    """Where the chatbot tables live, for raw SQL: the scratch copy under the test suite
    (`<default>_chatbot`, `tests/_pg_fixture.py`), `chatbot` everywhere else. The ALTERs are
    raw because alembic renders an ALTER TABLE's schema literally, past the test suite's
    `schema_translate_map` (the same trap `chat_topics_0001_taxonomy._schema` names)."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return _SCHEMA if current in (None, "public") else f"{current}_{_SCHEMA}"


def upgrade() -> None:
    facts = f'"{_schema(op.get_bind())}".chat_ask_facts'
    op.execute(f"ALTER TABLE {facts} ALTER COLUMN turn_id DROP NOT NULL")
    op.execute(f"ALTER TABLE {facts} ADD COLUMN IF NOT EXISTS history_id BIGINT")
    op.execute(f"ALTER TABLE {facts} DROP CONSTRAINT IF EXISTS ck_chat_ask_facts_one_source")
    op.execute(
        f"ALTER TABLE {facts} ADD CONSTRAINT ck_chat_ask_facts_one_source "
        "CHECK ((turn_id IS NULL) <> (history_id IS NULL))"
    )
    op.execute(f"ALTER TABLE {facts} DROP CONSTRAINT IF EXISTS uq_chat_ask_facts_history_seq")
    op.execute(
        f"ALTER TABLE {facts} ADD CONSTRAINT uq_chat_ask_facts_history_seq "
        "UNIQUE (history_id, ask_seq)"
    )

    op.create_table(
        "chat_backfill_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("era", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("messages_eligible", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("messages_read", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("messages_skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("asks_written", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("taxonomy_version", sa.SmallInteger(), nullable=True),
        sa.Column("row_limit", sa.Integer(), nullable=True),
        sa.Column("last_history_id", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        schema=_SCHEMA,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("chat_backfill_runs", schema=_SCHEMA, if_exists=True)
    facts = f'"{_schema(op.get_bind())}".chat_ask_facts'
    op.execute(f"DELETE FROM {facts} WHERE turn_id IS NULL")
    op.execute(f"ALTER TABLE {facts} DROP CONSTRAINT IF EXISTS uq_chat_ask_facts_history_seq")
    op.execute(f"ALTER TABLE {facts} DROP CONSTRAINT IF EXISTS ck_chat_ask_facts_one_source")
    op.execute(f"ALTER TABLE {facts} DROP COLUMN IF EXISTS history_id")
    op.execute(f"ALTER TABLE {facts} ALTER COLUMN turn_id SET NOT NULL")
