"""Publish the chatbot parser prompt with the `account` entity key as a NEW version,
label unmoved (PLAN-account-ledger-2oct.md, Design 4).

Same pattern as `chatbot_self_reference_vocab`: `ai_prompt_registry.render()` reads the
PUBLISHED row, not the Python constant, so the ACCOUNT_LEDGER_ADDENDUM only reaches a live
turn once the owner moves the `production` label onto the new version in the admin UI;
rolling back is the reverse move. The insert helper is repeated, not imported, because a
migration module must not depend on another migration's module staying importable.

Revision ID: acct_ledger_0002_vocab
Revises: acct_ledger_0001
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS

revision = "acct_ledger_0002_vocab"
down_revision = "acct_ledger_0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"


def _full_text() -> str:
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    return SEMANTIC_PARSER_PROMPT


def publish(session: Session) -> int | None:
    """Publish the body as the next version unless one already carries it. Returns the
    new version number, or None when already published (idempotent)."""
    template = _full_text()
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("chatbot parser account prompt already published as v%s", existing.version)
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=list(PROMPT_KEYS[PROMPT_NAME].variables),
        )
    )
    session.commit()
    logger.info(
        "published chatbot parser account prompt as v%s (%s chars); production label "
        "left on the previous version, promote by moving it",
        next_version,
        len(template),
    )
    return next_version


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
    seed_prompt_registry(bind)
    session = Session(bind=bind)
    try:
        publish(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    """Drop the version this migration published, unless a label now points at it (a
    label row cascades with its version, and a downgrade must never delete a label)."""
    session = Session(bind=op.get_bind())
    try:
        labelled = {
            row[0]
            for row in session.query(AIPromptLabel.version_id)
            .join(AIPromptVersion, AIPromptVersion.id == AIPromptLabel.version_id)
            .filter(AIPromptVersion.name == PROMPT_NAME)
            .all()
        }
        query = session.query(AIPromptVersion).filter(
            AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == _full_text()
        )
        if labelled:
            query = query.filter(AIPromptVersion.id.notin_(labelled))
        query.delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
