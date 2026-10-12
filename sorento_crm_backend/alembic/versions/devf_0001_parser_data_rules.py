"""Publish v50 plus the DEV-FAILURES "which data answers the question" addendum as one
unlabelled chatbot parser version (documentation/plans/chatbot/PLAN-dev-failures-12oct.md).

Why: seven DEV-set failures on v50 were parse misses where the meaning was read but mapped to
the wrong records ("when will there be stock" read as a stock check, "when will it be
delivered" missing the outstanding status, a price tag request read as the template file, a
brand promotion file read as a resource attachment, two brands read as one). The addendum
states which records answer each shape, as business rules with English examples only (owner
rule 11 Oct: teach business facts, not language).

The body is `alembic/data/chatbot_semantic_parser.v50.txt` (never edited) followed by
`alembic/data/chatbot_semantic_parser.dev-failures.addendum.txt`, with no policy blocks (the
graded v50 carries none). The production label is never moved and `ai_prompt_labels` is never
touched: promoting is one label move in the Chatbot Console, and the owner decides.

Idempotent: a version already carrying this exact template publishes nothing.

Revision ID: devf_0001_parser_data_rules
Revises: family_code_0001
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "devf_0001_parser_data_rules"
down_revision = "family_code_0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
DATA = Path(__file__).resolve().parents[1] / "data"
BASE = DATA / "chatbot_semantic_parser.v50.txt"
ADDENDUM = DATA / "chatbot_semantic_parser.dev-failures.addendum.txt"
MESSAGE = "devf_0001_parser_data_rules: v50 plus which-data-answers rules (DEV-FAILURES RC-A), unlabelled"


def body() -> str:
    return BASE.read_text(encoding="utf-8").rstrip("\n") + ADDENDUM.read_text(encoding="utf-8")


def publish(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion

    template = body()
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("devf_0001: body already published as v%s", existing.version)
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
    logger.info("devf_0001: published chatbot_semantic_parser v%s, label unmoved", next_version)
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
