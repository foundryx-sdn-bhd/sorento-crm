"""Index order_lines (company_id, from_doc_no) (SO-DELIVERIES-VIEW).

``documentation/plans/scm/PLAN-so-deliveries-view-14oct.md``. The deliveries read of one sales
order looks DO lines up by the SO number they name; without this index that is a scan of
order_lines. Same build shape as mig_idx_chunks_document_id: CONCURRENTLY, bounded lock wait,
an INVALID leftover dropped and rebuilt.

Revision ID: sodv_0001_ol_from_doc_idx
Revises: qlg_0001_drawing_attachment
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sodv_0001_ol_from_doc_idx"
down_revision = "qlg_0001_drawing_attachment"
branch_labels = None
depends_on = None

INDEX = "ix_order_lines_company_from_doc_no"
INDEX_LOCK_TIMEOUT = "30s"


def _index_valid(bind):
    """pg_index.indisvalid for the index (True / False), or None when there is none."""
    return bind.execute(
        sa.text(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = 'order_lines'::regclass AND c.relname = :name"
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
    bind.execute(sa.text(f"CREATE INDEX {c} IF NOT EXISTS {INDEX} ON order_lines (company_id, from_doc_no)"))
    if _index_valid(bind) is not True:
        raise RuntimeError(f"sodv_0001: index {INDEX} is INVALID after the build; drop it and rerun.")


def upgrade():
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        previous = bind.execute(sa.text("SELECT current_setting('lock_timeout')")).scalar()
        bind.execute(sa.text(f"SET lock_timeout = '{INDEX_LOCK_TIMEOUT}'"))
        try:
            _ensure_index(bind, concurrently=True)
        finally:
            bind.execute(sa.text("SELECT set_config('lock_timeout', :v, false)"), {"v": previous})


def downgrade():
    with op.get_context().autocommit_block():
        op.get_bind().execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}"))
