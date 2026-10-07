"""Teach the parser the `promotion_kind` output key as a NEW prompt version, label unmoved
(PROMO-SUBJECT-FILTER R5, documentation/plans/chatbot/PLAN-promo-subject-filter-07oct.md).

One exact wording edit applied to the production text through
`chatbot_prompt_vars.publish_wording_edit`, so the owner's edits and variables are kept. The
kinds themselves are not in the wording: the parser reads them each turn from the
`Promotion kinds:` line, built from `promotion_types`. The production label is never moved;
promoting is one label move in the Chatbot Console. Data only, idempotent (a text that no
longer carries the anchor publishes nothing).

Revision ID: pk_0001_parser_promo_kind
Revises: aig_0001_chatbot_allowed
"""
import logging

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "pk_0001_parser_promo_kind"
down_revision = "aig_0001_chatbot_allowed"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

MESSAGE = "pk_0001_parser_promo_kind: promotion_kind output key"
ANCHOR = "Otherwise false. Do NOT emit flyer as an entity; downstream handles it.\n"
ADDENDUM = (
    "\n== PROMOTION KIND ==\n"
    'The "Promotion kinds:" line lists the kinds of promotion as `code (name)`. When the current message\n'
    'asks for one of those kinds ("A3 flyer", "special", "focus item"), set top-level "promotion_kind"\n'
    "to that kind's code. A bare word that names no listed kind (\"flyer\", \"brochure\"), or no kind at all,\n"
    'is "promotion_kind": null. Never invent a code.\n'
)


def upgrade() -> None:
    from app.services.chatbot_prompt_vars import publish_wording_edit

    session = Session(bind=op.get_bind())
    version = publish_wording_edit(session, old=ANCHOR, new=ANCHOR + ADDENDUM, message=MESSAGE)
    if version is None:
        logger.info("pk_0001: anchor not in the production parser text; nothing published")
    else:
        logger.info("pk_0001: published chatbot_semantic_parser v%s, label unmoved", version)


def downgrade() -> None:
    op.get_bind().execute(
        text(
            "DELETE FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser' "
            "AND commit_message = :m "
            "AND id NOT IN (SELECT version_id FROM ai_prompt_labels)"
        ),
        {"m": MESSAGE},
    )
