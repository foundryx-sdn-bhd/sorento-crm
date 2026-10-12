"""Publish the REFERENCE KINDS parser prompt rebuilt on the PROMPT-ABLATION text (v56),
label unmoved (REF-PROBES, documentation/plans/chatbot/PLAN-ref-probes-11oct.md).

Why: `rkind_0001_parser_ref_kinds` publishes the code constant (v50 lineage) plus REF_KINDS,
which is what the per-step eval graded (eval v58). The promotion candidate is the ablation text
`pab_0001_parser_ablation` publishes (eval v56), which carries neither CHAT_REPORT_ADDENDUM (CA-3)
nor REF_KINDS_ADDENDUM. This publishes v56 with both, beneath PO_REFS_ADDENDUM in the code's
order, so promotion is one label move. No policy blocks (v56 carries none).

The text is `alembic/data/chatbot_semantic_parser.refkinds-v56-20261011.txt`, published verbatim:
the ablation file with `PO_REFS_ADDENDUM` replaced by `PO_REFS_ADDENDUM + CHAT_REPORT_ADDENDUM +
REF_KINDS_ADDENDUM`, and the entity format line's hint values extended with the five reference
kinds, one example each (crew decision 12 Oct 2026: with the addendum alone the parser kept
emitting "order" for a kind-worded number), and one PO REFS line saying a number emitted as an
order/document entity is never a po_ref (crew 12 Oct 2026; the parser decides, no code-side
prefix matching). `tests/chatbot/test_ref_kinds_v56_publish.py` pins all three edits.

The production label is never moved and `ai_prompt_labels` is never touched. Idempotent: a
version already carrying this exact template publishes nothing.

Revision ID: rkind_0002_parser_ref_kinds_v56
Revises: rkind_0001_parser_ref_kinds
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "rkind_0002_parser_ref_kinds_v56"
down_revision = "rkind_0001_parser_ref_kinds"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
SOURCE = Path(__file__).resolve().parents[1] / "data" / "chatbot_semantic_parser.refkinds-v56-20261011.txt"
MESSAGE = "rkind_0002_parser_ref_kinds_v56: v56 ablation text plus CHAT_REPORT and REF_KINDS (REF-PROBES), unlabelled"


def publish(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion

    template = SOURCE.read_text(encoding="utf-8")
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("rkind_0002: v56 reference kinds text already published as v%s", existing.version)
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=["current_date"],
            config_json=None,
            commit_message=MESSAGE,
        )
    )
    session.flush()
    logger.info("rkind_0002: published chatbot_semantic_parser v%s, label unmoved", next_version)
    return next_version


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
    # v1 from the fallback plus the production label, if this install never seeded the key.
    seed_prompt_registry(bind)
    session = Session(bind=bind)
    try:
        publish(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser' "
            "AND commit_message = :m "
            "AND id NOT IN (SELECT version_id FROM ai_prompt_labels)"
        ),
        {"m": MESSAGE},
    )
