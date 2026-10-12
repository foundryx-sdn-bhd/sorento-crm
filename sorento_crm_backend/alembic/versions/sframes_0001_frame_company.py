"""Backfill company_id on conversation_frame embeddings (SCOPE-FRAMES, finding 1).

Until this lane the embedding worker wrote every `conversation_frame` document and chunk
company-less, and a company-less row is visible under every company scope, so a
contact-scoped `/external/rag/search` could read every company's contact conversation
summaries. The worker now stamps the frame contact's company and the search hides a
company-less frame from any company scope; this backfill gives the existing rows their
company so they stay retrievable for their own company.

Additive UPDATE only: NULL -> the company of the frame's contact, when the contact
belongs to exactly one company (the same resolution the worker uses,
`embedding_worker.backfill_frame_embedding_companies`). Every other row stays NULL,
which now reads as hidden, not shared. Plan:
documentation/plans/security/PLAN-embedding-scope-frames-12oct.md.

Revision ID: sframes_0001_frame_company
Revises: devf_0002_portal_link_copy
"""
import logging

from alembic import op
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


def downgrade() -> None:
    # Left stamped: clearing the company would make these rows company-less again,
    # which is exactly the state this revision exists to end.
    pass
