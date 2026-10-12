"""Backfill company_id on conversation_frame embeddings (SCOPE-FRAMES, finding 1) and
re-embed the certificates that carry another company's product codes (finding 3).

Until this lane the embedding worker wrote every `conversation_frame` document and chunk
company-less, and a company-less row is visible under every company scope, so a
contact-scoped `/external/rag/search` could read every company's contact conversation
summaries. The worker now stamps the frame contact's company and the search hides a
company-less frame from any company scope; this backfill gives the existing rows their
company so they stay retrievable for their own company.

Additive UPDATE only: NULL -> the company of the frame's contact, when the contact
belongs to exactly one company (the same resolution the worker uses,
`embedding_worker.backfill_frame_embedding_companies`). Every other row stays NULL,
which now reads as hidden, not shared.

Certificates: the worker now embeds only the codes of the certificate's own company's
products (a shared certificate: company-less products only). An embedding written before
that keeps the other companies' codes until it is rebuilt, so every certificate covering a
product of a different company (or any company-owned product, for a shared certificate) is
queued for a rebuild; the scheduler's embedding drain picks the rows up. Plan:
documentation/plans/security/PLAN-embedding-scope-frames-12oct.md.

Revision ID: sframes_0001_frame_company
Revises: devf_0002_portal_link_copy
"""
import logging

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "sframes_0001_frame_company"
down_revision = "devf_0002_portal_link_copy"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    from app.services.embedding_worker import backfill_frame_embedding_companies

    session = Session(bind=op.get_bind())
    try:
        stamped = backfill_frame_embedding_companies(session)
        session.commit()
        logger.info("stamped company_id on %s conversation_frame embedding document(s)", stamped)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    from app.services.embedding_change_listener import bulk_enqueue_embedding_events

    bind = op.get_bind()
    cert_ids = [
        r[0]
        for r in bind.execute(
            text(
                """
                SELECT DISTINCT c.id::text
                FROM certificates c
                JOIN certificate_products cp ON cp.certificate_id = c.id
                JOIN products p ON p.id = cp.product_id
                WHERE p.company_id IS DISTINCT FROM c.company_id
                  AND EXISTS (
                      SELECT 1 FROM embedding_documents d
                      WHERE d.source_type = 'certificate' AND d.source_id = c.id::text
                  )
                """
            )
        )
    ]
    queued = bulk_enqueue_embedding_events(
        bind, "certificate", cert_ids, event_type="embedding.rebuild_requested"
    )
    logger.info("queued %s certificate embedding rebuild(s)", queued)


def downgrade() -> None:
    # Left stamped: clearing the company would make these rows company-less again,
    # which is exactly the state this revision exists to end.
    pass
