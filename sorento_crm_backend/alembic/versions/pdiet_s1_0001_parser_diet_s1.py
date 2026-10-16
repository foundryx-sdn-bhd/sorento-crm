"""Publish the PROMPT-DIET stage 1 chatbot parser version, label unmoved
(documentation/plans/chatbot/prompt-diet-s1-acceptance-criteria.md).

Why: dev v94 (= prod v63 text) minus the PROMPT-ABLATION cuts that still match, the dead
sections (FLYER FLAG instruction, CORRECTION / DOUBT, REFERENCE TARGET), the section 43
contradiction, and the banner boxes that only announced OUTPUT keys, which now sit inside the
OUTPUT object. The text is `alembic/data/chatbot_semantic_parser.diet-s1-20261016.txt`, published
verbatim.

The production label is never moved and `ai_prompt_labels` is never touched: promotion is one
label move in the Chatbot Console, and the owner decides. To rebuild the data file run
`venv/bin/python -m scripts.build_parser_diet` from sorento_crm_backend/.

Idempotent: a version already carrying this exact template publishes nothing.

Revision ID: pdiet_s1_0001
Revises: cfs_0001_spec_registry_seed
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "pdiet_s1_0001"
down_revision = "cfs_0001_spec_registry_seed"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
SOURCE = Path(__file__).resolve().parents[1] / "data" / "chatbot_semantic_parser.diet-s1-20261016.txt"
MESSAGE = "pdiet_s1_0001: PROMPT-DIET stage 1, v94 text with the #1574 cuts, dead sections and banner boxes removed, unlabelled"


def publish(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion

    template = SOURCE.read_text(encoding="utf-8")
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("pdiet_s1_0001: diet text already published as v%s", existing.version)
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=["current_date", "find_filters"],
            config_json=None,
            commit_message=MESSAGE,
        )
    )
    session.flush()
    logger.info("pdiet_s1_0001: published chatbot_semantic_parser v%s, label unmoved", next_version)
    return next_version


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
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
