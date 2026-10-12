"""Publish main's trimmed parser (v56) plus the DEV-FAILURES addenda as one unlabelled chatbot
parser version (documentation/plans/chatbot/PLAN-dev-failures-12oct.md).

Why: seven DEV-set failures were parse misses where the meaning was read but mapped to the
wrong records, and two portal link replies could not say which form the link opens. The
addenda are one short business rule per gap, at most one English example each, no word lists
(owner 12 Oct, after the #1542 audit): `chatbot_semantic_parser.dev-failures.addendum.txt`
(RC-A) and `chatbot_semantic_parser.portal-form.addendum.txt` (RC-B, the `portal_form` key).

The body is `alembic/data/chatbot_semantic_parser.ablation-20261011.txt` (the v56 text
`pab_0001_parser_ablation` publishes, never edited) followed by the two addenda, with no
policy blocks (the graded v56 carries none). The production label is never moved and `ai_prompt_labels` is never
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
BASE = DATA / "chatbot_semantic_parser.ablation-20261011.txt"
ADDENDA = (
    DATA / "chatbot_semantic_parser.dev-failures.addendum.txt",
    DATA / "chatbot_semantic_parser.portal-form.addendum.txt",
)
MESSAGE = "devf_0001_parser_data_rules: v56 plus which-records rules and portal_form (DEV-FAILURES), unlabelled"


def body() -> str:
    return BASE.read_text(encoding="utf-8").rstrip("\n") + "".join(a.read_text(encoding="utf-8") for a in ADDENDA)


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
