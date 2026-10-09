"""Publish the chatbot parser prompt with the SUB ASKS and FAMILY keys as a NEW version,
label unmoved (MULTI-ASK-PLAN S2, documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md).

`app/services/chatbot_parser_prompt.SUB_ASKS_ADDENDUM` teaches `sub_asks` (every thing a
message asks, in order, when it asks two or more) and `family` (a typed code means the exact
code or its whole family). The engine's per-ask runner (`turn/sub_asks.py`) answers a
two-or-more-ask message from them.

Published with `chatbot_prompt_vars.publish_wording_edit`, the way a migration after the
wording layer changes the parser (the drift test refuses a version rebuilt from the code
constant): one exact edit to the PRODUCTION text, owner edits and variables kept, as a new
UNLABELLED version. The addendum goes right after `PO_REFS_ADDENDUM` when production carries
it, else right before the MEMORY block, else right before the rendered policy blocks
(`<<<CHATBOT POLICY BLOCKS>>>`, how dev's v60 ends). A production text with none of the three
fails the migration loudly. The one silent case is a freshly bootstrapped database, whose production
text predates the wording layer (an edit of it would carry the registry lists as literal
text, which the drift test refuses): it is skipped with a warning.
Promoting is one label move in the admin UI, after crew's live parser checks; rolling back
is the reverse move. Until then the strict output schema already carries both keys (absent
on the old prompt, tolerated), so nothing changes.

Idempotent: skipped when a version already carries the addendum.

Revision ID: maskp_0001_parser_sub_asks
Revises: psl_0001_chatbot_file_links
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

revision = "maskp_0001_parser_sub_asks"
down_revision = "psl_0001_chatbot_file_links"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
#: The head of MEMORY_ADDENDUM, the production text's tail block.
MEMORY_ANCHOR = "\n\n== MEMORY ==\n"
#: The rendered policy blocks every version since `chatbot_rearch_s4` ends with
#: (`chatbot_parser_prompt.BLOCKS_BEGIN`). Dev's production text (v60, 10 Oct 2026) carries
#: neither PO_REFS_ADDENDUM nor the MEMORY block, but ends with these.
BLOCKS_ANCHOR = "\n\n<<<CHATBOT POLICY BLOCKS>>>"
MESSAGE = (
    "MULTI-ASK-PLAN: the sub_asks and family keys (one entry per thing a message asks; "
    "exact code or family). Unlabelled, promote from the Prompts page."
)


def publish(session: Session) -> int | None:
    from app.services.chatbot_parser_prompt import PO_REFS_ADDENDUM, SUB_ASKS_ADDENDUM
    from app.services.chatbot_prompt_vars import publish_wording_edit

    already = (
        session.query(AIPromptVersion.version)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template.contains(SUB_ASKS_ADDENDUM))
        .first()
    )
    if already is not None:
        logger.info("chatbot parser sub asks prompt already published as v%s", already[0])
        return None
    from sqlalchemy import text as sql

    row = session.execute(
        sql(
            "SELECT v.template, v.version FROM ai_prompt_versions v JOIN ai_prompt_labels l ON l.version_id = v.id "
            "WHERE l.name = :n AND l.label = 'production'"
        ),
        {"n": PROMPT_NAME},
    ).first()
    production, production_version = (row[0], int(row[1])) if row else (None, None)
    layer = session.execute(
        sql(
            "SELECT min(version) FROM ai_prompt_versions "
            "WHERE name = :n AND (config_json::jsonb) ? 'wording_layer_report'"
        ),
        {"n": PROMPT_NAME},
    ).scalar()
    if production_version is not None and layer is not None and production_version < int(layer):
        # Production predates the wording layer (a freshly bootstrapped database, where the
        # wording layer's own version is published unlabelled). An edit of it would carry
        # the registry lists as literal text, which the drift test refuses.
        logger.warning(
            "chatbot parser sub asks prompt NOT published: production v%s predates the "
            "wording layer v%s; promote that first, then re-run this publish",
            production_version,
            layer,
        )
        return None
    # The anchor (crew live check, 9 Oct 2026: dev's production text, v60, had no
    # PO_REFS_ADDENDUM, so the first form of this publish wrote nothing and said so only
    # in a warning). After PO_REFS_ADDENDUM when the production text carries it (its place
    # in the code constant), else right before the MEMORY block, which every production
    # text since `mem_0002_parser_memory` carries and which stays the tail, else right
    # before the policy blocks (dev v60 has neither of the first two, crew 10 Oct). None of
    # the three: the deploy fails here, loudly, rather than shipping a runner the parser
    # never feeds.
    if production is None:
        raise RuntimeError("maskp_0001: no production-labelled chatbot_semantic_parser version")
    if PO_REFS_ADDENDUM in production:
        old, new = PO_REFS_ADDENDUM, PO_REFS_ADDENDUM + SUB_ASKS_ADDENDUM
    elif MEMORY_ANCHOR in production:
        old, new = MEMORY_ANCHOR, SUB_ASKS_ADDENDUM + MEMORY_ANCHOR
    elif BLOCKS_ANCHOR in production:
        old, new = BLOCKS_ANCHOR, SUB_ASKS_ADDENDUM + BLOCKS_ANCHOR
    else:
        raise RuntimeError(
            "maskp_0001: the production parser text carries none of PO_REFS_ADDENDUM, the "
            "MEMORY block or the policy blocks, so the sub asks addendum has no place to go; "
            "publish it by hand"
        )
    version = publish_wording_edit(session, old=old, new=new, message=MESSAGE)
    if version is None:
        raise RuntimeError("maskp_0001: publish_wording_edit found no production text to edit")
    session.commit()
    logger.info("published chatbot parser sub asks prompt as v%s; production label unmoved", version)
    return version


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
    """Drop the unlabelled version this published, unless the owner has since moved a label
    onto it (a downgrade must never delete a label)."""
    from app.services.chatbot_parser_prompt import SUB_ASKS_ADDENDUM

    session = Session(bind=op.get_bind())
    try:
        labelled = {row.version_id for row in session.query(AIPromptLabel)}
        for row in session.query(AIPromptVersion).filter(
            AIPromptVersion.name == PROMPT_NAME,
            AIPromptVersion.template.contains(SUB_ASKS_ADDENDUM),
            AIPromptVersion.commit_message == MESSAGE,
        ):
            if row.id not in labelled:
                session.delete(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
