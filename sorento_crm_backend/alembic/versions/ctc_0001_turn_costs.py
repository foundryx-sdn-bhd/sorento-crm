"""CHAT-TURN-COST: per-turn token + cost datasets for the analytics connector

Revision ID: ctc_0001_turn_costs
Revises: mig_idx_chunks_document_id
Create Date: 2026-10-13 00:00:00.000000

Plan: documentation/plans/analytics/PLAN-chat-turn-cost-13oct.md. Additive only.

1. `ai_assistant_usage_logs.cached_tokens` (nullable), backfilled once for chatbot parser rows
   from their turn's `understood` trace facts.
2. `chatbot.chat_ask_facts.prompt_version` (nullable), backfilled the same way, so the
   `turn_costs` view reads category, topic and prompt version off the fact row, never the trace.
3. `llm_model_prices`, seeded with the prices the repo cites with a source
   (`app.services.analytics_connector.prices.SEED_PRICES`); other models stay unpriced.
4. Views `analytics_connector.llm_calls` and `analytics_connector.turn_costs`
   (`views.ensure_cost_views`, also run by scripts/bootstrap_env.py), granted to the read-only
   role when it exists.
5. Index `ix_ai_assistant_usage_logs_chatbot_turn_id`, built CONCURRENTLY.
"""
import sqlalchemy as sa
from alembic import op

from app.services.analytics_connector import backfill, prices, views

revision = "ctc_0001_turn_costs"
down_revision = "mig_idx_chunks_document_id"
branch_labels = None
depends_on = None

INDEX = "ix_ai_assistant_usage_logs_chatbot_turn_id"
INDEX_LOCK_TIMEOUT = "30s"


def _index_valid(bind):
    return bind.execute(
        sa.text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = 'ai_assistant_usage_logs'::regclass AND c.relname = :name"
        ),
        {"name": INDEX},
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    # The ALTERs take ACCESS EXCLUSIVE on two tables the live chatbot writes every turn: fail the
    # release rather than queue those writes behind a long reader (repo precedent qr_0001).
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text("ALTER TABLE ai_assistant_usage_logs ADD COLUMN IF NOT EXISTS cached_tokens INTEGER"))
    bind.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS llm_model_prices ("
            " id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
            " model VARCHAR(128) NOT NULL UNIQUE,"
            " input_usd_per_1m NUMERIC(12, 6) NOT NULL,"
            " cached_input_usd_per_1m NUMERIC(12, 6),"
            " output_usd_per_1m NUMERIC(12, 6) NOT NULL,"
            " note TEXT,"
            " updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now())"
        )
    )
    bind.execute(sa.text("ALTER TABLE chatbot.chat_ask_facts ADD COLUMN IF NOT EXISTS prompt_version INTEGER"))
    prices.seed_prices(bind)
    views.ensure_cost_views(bind)
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": views.RO_ROLE}).scalar():
        views.grant_read_role(bind, views.RO_ROLE)

    # The backfills read every turn trace once (seconds on prod volume), so they run AFTER the
    # ALTERs commit: each UPDATE in its own transaction, holding row locks only.
    with op.get_context().autocommit_block():
        backfill.backfill_cached_tokens(bind)
        backfill.backfill_prompt_version(bind)

    # Same shape as mig_idx_chunks_document_id: CONCURRENTLY outside the transaction, a bounded
    # lock wait, and an INVALID leftover of a failed build dropped and rebuilt.
    with op.get_context().autocommit_block():
        if _index_valid(bind) is True:
            return
        previous = bind.execute(sa.text("SELECT current_setting('lock_timeout')")).scalar()
        bind.execute(sa.text(f"SET lock_timeout = '{INDEX_LOCK_TIMEOUT}'"))
        try:
            if _index_valid(bind) is False:
                bind.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}"))
            bind.execute(
                sa.text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON ai_assistant_usage_logs (chatbot_turn_id)")
            )
        finally:
            bind.execute(sa.text("SELECT set_config('lock_timeout', :v, false)"), {"v": previous})
        if _index_valid(bind) is not True:
            raise RuntimeError(f"ctc_0001_turn_costs: index {INDEX} is INVALID after the build; drop it and rerun.")


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {views.SCHEMA}.turn_costs")
    op.execute(f"DROP VIEW IF EXISTS {views.SCHEMA}.llm_calls")
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.execute("DROP TABLE IF EXISTS llm_model_prices")
    op.execute("ALTER TABLE chatbot.chat_ask_facts DROP COLUMN IF EXISTS prompt_version")
    op.execute("ALTER TABLE ai_assistant_usage_logs DROP COLUMN IF EXISTS cached_tokens")
