"""Give `chatbot.chat_topics` a uuid surrogate `id`, keep `code` as a unique key.

Design principle (tests/test_schema_uuid_id_principle.py): every domain table has a uuid `id`
primary key. `chat_topics_0001_taxonomy` keyed the taxonomy on `code`; this is the
`298_market_segments_uuid_id` treatment. `code` stays UNIQUE NOT NULL and the two FKs that
reference it (`chat_topics.parent_code`, `chat_label_audits.corrected_topic`) keep pointing at
it. Only the PK moves to `id`.

Sequence matters: the FKs depend on the `code` primary-key index, so Postgres refuses to drop
that PK while they exist. Give `code` its own unique constraint, drop the FKs, swap the PK,
re-create the FKs against the unique constraint. Idempotent: a database that `create_all`
already converged (PK on `id`) is left as it is.

Revision ID: chat_topics_0003_uuid_id
Revises: ppl_0001_parser_candidate
"""
from alembic import op

revision = "chat_topics_0003_uuid_id"
down_revision = "ppl_0001_parser_candidate"
branch_labels = None
depends_on = None

_SCHEMA = "chatbot"

# (table, constraint, column) for every FK onto chat_topics.code.
_FKS = (
    ("chat_topics", "chat_topics_parent_code_fkey", "parent_code"),
    ("chat_label_audits", "chat_label_audits_corrected_topic_fkey", "corrected_topic"),
)


def _schema(bind) -> str:
    """Where the chatbot tables live, for raw SQL: the scratch copy under the test suite
    (`<default>_chatbot`, `tests/_pg_fixture.py`), `chatbot` everywhere else."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return _SCHEMA if current in (None, "public") else f"{current}_{_SCHEMA}"


def _pk_columns(bind, schema: str) -> list[str]:
    return [
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indisprimary AND i.indrelid = %s::regclass",
            (f'"{schema}".chat_topics',),
        )
    ]


def _drop_fks(schema: str) -> None:
    for table, fk, _col in _FKS:
        op.execute(f'ALTER TABLE "{schema}".{table} DROP CONSTRAINT IF EXISTS {fk}')


def _add_fks(schema: str) -> None:
    for table, fk, col in _FKS:
        op.execute(
            f'ALTER TABLE "{schema}".{table} ADD CONSTRAINT {fk} '
            f'FOREIGN KEY ({col}) REFERENCES "{schema}".chat_topics (code)'
        )


def _set_pk(schema: str, column: str) -> None:
    op.execute(f'ALTER TABLE "{schema}".chat_topics DROP CONSTRAINT IF EXISTS chat_topics_pkey')
    op.execute(f'ALTER TABLE "{schema}".chat_topics ADD CONSTRAINT chat_topics_pkey PRIMARY KEY ({column})')


def upgrade() -> None:
    bind = op.get_bind()
    schema = _schema(bind)
    # 1. Surrogate id, backfilled for the seeded rows by the default.
    op.execute(
        f'ALTER TABLE "{schema}".chat_topics '
        "ADD COLUMN IF NOT EXISTS id uuid NOT NULL DEFAULT gen_random_uuid()"
    )
    # 2. Unique constraint on code so the FKs have a target once the PK moves off it.
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_chat_topics_code'
                  AND conrelid = '"{schema}".chat_topics'::regclass
            ) THEN
                ALTER TABLE "{schema}".chat_topics ADD CONSTRAINT uq_chat_topics_code UNIQUE (code);
            END IF;
        END $$;
        """
    )
    # 3-5. Drop the FKs, swap the PK onto id, re-create the FKs against code.
    if _pk_columns(bind, schema) != ["id"]:
        _drop_fks(schema)
        _set_pk(schema, "id")
        _add_fks(schema)


def downgrade() -> None:
    bind = op.get_bind()
    schema = _schema(bind)
    # The FKs may ride either unique index on code, so they go before either is touched.
    _drop_fks(schema)
    if _pk_columns(bind, schema) != ["code"]:
        _set_pk(schema, "code")
    op.execute(f'ALTER TABLE "{schema}".chat_topics DROP CONSTRAINT IF EXISTS uq_chat_topics_code')
    op.execute(f'ALTER TABLE "{schema}".chat_topics DROP COLUMN IF EXISTS id')
    _add_fks(schema)
