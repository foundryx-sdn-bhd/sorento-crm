"""Publish ONE consolidated chatbot parser version built from the code constant, label unmoved
(PROMO-SUBJECT-FILTER R5 and owner review round 2, AC-26 / AC-27,
documentation/plans/chatbot/PLAN-promo-subject-filter-07oct.md).

The body is `SEMANTIC_PARSER_PROMPT` as the code builds it, so it carries every current
addendum: the `promotion_kind` output key (the PROMOTION KIND section, kinds read each turn
from the `Promotion kinds:` line) and the PROMOTION SUBJECT grounding (a "seat cover" is a
toilet seat). Same publish as `report_engine_0001_prompt`; the helper is repeated, not
imported, because a migration must not depend on another migration's module. The production
label is never moved; promoting is one label move in the Chatbot Console. Idempotent: a body
already published publishes nothing.

Revision ID: pk_0001_parser_promo_kind
Revises: report_drill_0001_prompt
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "pk_0001_parser_promo_kind"
down_revision = "report_drill_0001_prompt"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
MESSAGE = "pk_0001_parser_promo_kind: promotion kind + promotion subject (seat cover -> toilet seat)"
#: The sections this version exists to carry; the constant must hold both.
REQUIRED = ("== PROMOTION KIND ==", "== PROMOTION SUBJECT ==", '"seat cover" is a toilet seat')


def publish(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion
    from app.services.ai_prompt_registry import PROMPT_KEYS
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    template = SEMANTIC_PARSER_PROMPT
    missing = [marker for marker in REQUIRED if marker not in template]
    if missing:
        logger.warning("pk_0001: the parser constant lacks %s; nothing published", missing)
        return None
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("pk_0001: parser body already published as v%s", existing.version)
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
            commit_message=MESSAGE,
        )
    )
    session.flush()
    logger.info("pk_0001: published chatbot_semantic_parser v%s, label unmoved", next_version)
    return next_version


def upgrade() -> None:
    session = Session(bind=op.get_bind())
    publish(session)


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser' "
            "AND commit_message = :m "
            "AND id NOT IN (SELECT version_id FROM ai_prompt_labels)"
        ),
        {"m": MESSAGE},
    )
