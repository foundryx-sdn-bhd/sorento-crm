"""Index embedding_chunks.document_id (MIGRATION-INDEX).

``documentation/plans/ci/PLAN-migration-index-13oct.md``. The foreign key to
``embedding_documents`` cascades on delete but had no index, so every deleted document cost a
sequential scan of the vector table (the scope_seeds_debtor_company release stalled on it).

scope_seeds_debtor_company now builds this index before its deletes. This revision builds it
for a database that had already run scope_seeds_debtor_company before that change (the stalled
release may have finished on its own), where a revision placed before it would never run.
Elsewhere it is a no-op.

Revision ID: mig_idx_chunks_document_id
Revises: scope_seeds_debtor_company
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "mig_idx_chunks_document_id"
down_revision = "scope_seeds_debtor_company"
branch_labels = None
depends_on = None

INDEX = "ix_embedding_chunks_document_id"
INDEX_LOCK_TIMEOUT = "30s"


def _index_valid(bind):
    """pg_index.indisvalid for the index (True / False), or None when there is none."""
    return bind.execute(
        sa.text(
            # Through the table, not to_regclass(name): an unqualified index name resolves along
            # the search_path, so a same-named index in a later schema would answer for it.
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = 'embedding_chunks'::regclass AND c.relname = :name"
        ),
        {"name": INDEX},
    ).scalar()


def _ensure_index(bind, *, concurrently: bool) -> None:
    """Build the index unless a VALID one exists; drop an INVALID leftover of a failed
    CONCURRENTLY build first, which IF NOT EXISTS would otherwise keep forever."""
    if _index_valid(bind) is True:
        return
    c = "CONCURRENTLY" if concurrently else ""
    if _index_valid(bind) is False:
        bind.execute(sa.text(f"DROP INDEX {c} IF EXISTS {INDEX}"))
    bind.execute(sa.text(f"CREATE INDEX {c} IF NOT EXISTS {INDEX} ON embedding_chunks (document_id)"))
    if _index_valid(bind) is not True:
        raise RuntimeError(f"mig_idx_chunks_document_id: index {INDEX} is INVALID after the build; drop it and rerun.")


def upgrade():
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction: writes to embedding_chunks
    # carry on while it builds.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        # The build waits for every open transaction on embedding_chunks (the live colour's
        # API and worker write to it). Bound that wait so a release fails instead of hanging;
        # the INVALID index a timeout leaves is dropped and rebuilt by the next run. Session
        # level, since SET LOCAL does nothing outside a transaction; restored after.
        previous = bind.execute(sa.text("SELECT current_setting('lock_timeout')")).scalar()
        bind.execute(sa.text(f"SET lock_timeout = '{INDEX_LOCK_TIMEOUT}'"))
        try:
            _ensure_index(bind, concurrently=True)
        finally:
            bind.execute(sa.text("SELECT set_config('lock_timeout', :v, false)"), {"v": previous})


def downgrade():
    # The index belongs to the model (EmbeddingChunk.document_id, index=True) and
    # scope_seeds_debtor_company builds it too, so stepping back past this revision keeps it.
    pass
