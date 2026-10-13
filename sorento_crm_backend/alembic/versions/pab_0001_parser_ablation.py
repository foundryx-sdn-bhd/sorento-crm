"""Publish the PROMPT-ABLATION trimmed chatbot parser version, label unmoved
(documentation/plans/chatbot/PLAN-prompt-ablation-11oct.md).

Why: v50 minus its language teaching (Malay and Chinese synonym lists, typo examples, number
words, honorifics) and the Chinese to English class map is shorter and cheaper per turn. Eval:
DEV 82.2% vs 84.1% (within noise), hidden gate 83.2% vs 80.8%, cost -2.5% on the hidden set and
-9% on DEV. The text is `alembic/data/chatbot_semantic_parser.ablation-20261011.txt`, published
verbatim with no policy blocks (the graded v50 and v56 carry none).

The production label is never moved and `ai_prompt_labels` is never touched: promotion is one
label move in the Chatbot Console, and the owner decides. To rebuild the data file run
`venv/bin/python -m scripts.build_parser_ablation` from sorento_crm_backend/.

Idempotent: a version already carrying this exact template publishes nothing.

Revision ID: pab_0001_parser_ablation
Revises: chat_backfill_0001
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "pab_0001_parser_ablation"
down_revision = "chat_backfill_0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
SOURCE = Path(__file__).resolve().parents[1] / "data" / "chatbot_semantic_parser.ablation-20261011.txt"
MESSAGE = "pab_0001_parser_ablation: v50 minus language teaching and the class map (PROMPT-ABLATION, eval v56 fe4f3509), unlabelled"


def publish(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion

    template = SOURCE.read_text(encoding="utf-8")
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("pab_0001: ablation text already published as v%s", existing.version)
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
    logger.info("pab_0001: published chatbot_semantic_parser v%s, label unmoved", next_version)
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
