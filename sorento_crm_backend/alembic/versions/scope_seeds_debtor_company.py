"""Per-company debtor seeds + certificate / product_spec embedding company backfill.

SCOPE-SEEDS (follow-up (a) of #1605,
``documentation/plans/chatbot/PLAN-embedding-company-seeds-12oct.md``). Data only, no DDL;
idempotent (every step is guarded by NOT EXISTS / IS NULL, so a second run is a no-op).

1. Synthetic customer seeds were written company-less as ``debtor:<code>`` (migration 304
   left them NULL), so since #1605 every company scope drops them. They become
   ``debtor:<company_id>:<code>`` rows with ``company_id`` set, one per company whose LIVE
   orders carry that code under the SAME name the legacy seed was embedded from, and whose
   company has no customer master row with that code (the backfill rule). The vector is
   reused, so the seed resolves straight after the deploy; the worker's source_hash
   includes the id, so the next backfill re-embeds these once (cost only). A company whose orders name the code
   differently gets no copy here: copying would put another company's debtor text in its
   row. The next embedding backfill (``POST /api/v1/system/embeddings/backfill`` or
   ``app/scripts/seed_embeddings.py``) seeds it from its own orders.
   The legacy company-less rows are then deleted (derived data; the worker now refuses a
   legacy id, so they cannot come back) and pending legacy queue items are skipped.
2. Migration 304 backfilled 15 owned source types but not ``certificate`` or
   ``product_spec`` (keyed on the product id), so their pre-304 rows read as shared on
   ``/external/rag/search``. Stamped from the source row; a NULL-company certificate is
   shared by design and stays NULL.

Counts are logged before anything changes (the dry run) and after.

Revision ID: scope_seeds_debtor_company
Revises: oihr_0006_no_remark_column
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "scope_seeds_debtor_company"
down_revision = "oihr_0006_no_remark_column"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# A per-company seed id: 'debtor:' + uuid + ':' + code. Anything else under 'debtor:' is
# a legacy company-less seed.
_NEW_ID = "'^debtor:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:'"
# The code inside a per-company id starts after 'debtor:' (7) + uuid (36) + ':' (1).
_CODE_FROM = 45


def _legacy(alias: str) -> str:
    return f"({alias}.source_type = 'customer' AND {alias}.source_id LIKE 'debtor:%' AND {alias}.source_id !~ {_NEW_ID})"


# (company, code, title) a live order carries; title is what the worker titles a seed
# with (`debtor_name or debtor_code`).
_PAIRS = """
    SELECT DISTINCT o.company_id, btrim(o.debtor_code) AS code,
           COALESCE(NULLIF(o.debtor_name, ''), o.debtor_code) AS title
    FROM orders o
    WHERE o.deleted_at IS NULL
      AND o.company_id IS NOT NULL
      AND o.debtor_code IS NOT NULL
      AND btrim(o.debtor_code) <> ''
      AND length(btrim(o.debtor_code)) <= 84  -- 44-char prefix + code must fit source_id varchar(128)
"""

# Legacy seed document d joined to each company p it can be copied to.
_COPYABLE = f"""
    FROM embedding_documents d
    JOIN ({_PAIRS}) p ON d.source_id = 'debtor:' || p.code AND d.title = p.title
    WHERE {_legacy('d')}
      AND NOT EXISTS (
          SELECT 1 FROM customers cu
          WHERE cu.company_id = p.company_id AND cu.customer_code = p.code
      )
      AND NOT EXISTS (
          SELECT 1 FROM embedding_documents x
          WHERE x.source_type = 'customer'
            AND x.source_id = 'debtor:' || p.company_id::text || ':' || p.code
      )
"""

_SOURCE_TABLE = {"certificate": "certificates", "product_spec": "products"}


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar() or 0)


def _counts() -> dict[str, int]:
    out = {
        "legacy_seed_documents": _count(f"SELECT count(*) FROM embedding_documents d WHERE {_legacy('d')}"),
        "legacy_seed_chunks": _count(f"SELECT count(*) FROM embedding_chunks d WHERE {_legacy('d')}"),
        "seed_copies_to_create": _count(f"SELECT count(*) {_COPYABLE}"),
        "legacy_queue_pending": _count(
            f"SELECT count(*) FROM embedding_queue d WHERE {_legacy('d')} AND d.status = 'pending'"
        ),
    }
    for source_type, table in _SOURCE_TABLE.items():
        for target in ("embedding_documents", "embedding_chunks"):
            out[f"{source_type}_{target}_to_stamp"] = _count(
                f"""
                SELECT count(*) FROM {target} e JOIN {table} s ON e.source_id = s.id::text
                WHERE e.source_type = '{source_type}' AND e.company_id IS NULL AND s.company_id IS NOT NULL
                """
            )
    return out


def upgrade():
    log.info("scope_seeds_debtor_company dry run (before): %s", _counts())

    # 1a. One per-company document per copyable (legacy seed, company).
    op.execute(
        f"""
        INSERT INTO embedding_documents (
            id, source_type, source_id, source_key, title, body_text, metadata,
            visibility_scope, source_hash, source_updated_at, is_active, company_id,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), 'customer',
               'debtor:' || p.company_id::text || ':' || p.code,
               'debtor:' || p.code, d.title, d.body_text, d.metadata,
               d.visibility_scope, d.source_hash, d.source_updated_at, d.is_active, p.company_id,
               now(), now()
        {_COPYABLE}
        """
    )
    # 1b. Its current chunks, vector reused, for every per-company document still without
    # chunks (just created above, or left chunk-less by an interrupted earlier run).
    op.execute(
        f"""
        INSERT INTO embedding_chunks (
            id, document_id, source_type, source_id, chunk_index, chunk_text, chunk_hash,
            embedding, model_name, model_version, embedding_provider, source_hash, metadata,
            company_id, is_current, embedded_at
        )
        SELECT gen_random_uuid(), nd.id, 'customer', nd.source_id, c.chunk_index, c.chunk_text,
               c.chunk_hash, c.embedding, c.model_name, c.model_version, c.embedding_provider,
               c.source_hash, c.metadata, nd.company_id, TRUE, c.embedded_at
        FROM embedding_documents nd
        JOIN embedding_documents od
          ON {_legacy('od')} AND od.source_id = 'debtor:' || substring(nd.source_id from {_CODE_FROM})
         AND od.title = nd.title
        JOIN embedding_chunks c ON c.document_id = od.id AND c.is_current = TRUE
        WHERE nd.source_type = 'customer'
          AND nd.source_id ~ {_NEW_ID}
          AND nd.company_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM embedding_chunks x WHERE x.document_id = nd.id)
        """
    )
    # 1c. Legacy company-less seeds go (chunks first: they reference the document).
    op.execute(f"DELETE FROM embedding_chunks d WHERE {_legacy('d')}")
    op.execute(f"DELETE FROM embedding_documents d WHERE {_legacy('d')}")
    op.execute(
        f"""
        UPDATE embedding_queue d
        SET status = 'skipped', processed_at = now(),
            last_error = 'legacy company-less debtor seed (scope_seeds_debtor_company)'
        WHERE {_legacy('d')} AND d.status = 'pending'
        """
    )

    # 2. certificate / product_spec rows embedded before migration 304.
    for target in ("embedding_documents", "embedding_chunks"):
        for source_type, table in _SOURCE_TABLE.items():
            op.execute(
                f"""
                UPDATE {target} e
                SET company_id = s.company_id
                FROM {table} s
                WHERE e.source_type = '{source_type}'
                  AND e.source_id = s.id::text
                  AND e.company_id IS NULL
                  AND s.company_id IS NOT NULL
                """
            )

    log.info("scope_seeds_debtor_company after: %s", _counts())


def downgrade():
    # Data only. The legacy company-less seeds are not restored: they read as shared
    # knowledge, which is the leak this revision closes.
    pass
