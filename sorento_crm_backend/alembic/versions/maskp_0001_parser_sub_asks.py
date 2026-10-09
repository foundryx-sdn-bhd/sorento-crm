"""Publish the chatbot parser prompt with the SUB ASKS and FAMILY keys as a NEW version,
label unmoved (MULTI-ASK-PLAN S2, documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md).

`app/services/chatbot_parser_prompt.SUB_ASKS_ADDENDUM` teaches `sub_asks` (every thing a
message asks, in order, when it asks two or more) and `family` (a typed code means the exact
code or its whole family). The engine's per-ask runner (`turn/sub_asks.py`) answers a
two-or-more-ask message from them.

Published with `chatbot_prompt_vars.publish_wording_edit`, the way a migration after the
wording layer changes the parser (the drift test refuses a version rebuilt from the code
constant): one exact edit to the PRODUCTION text, the addendum placed right after
`PO_REFS_ADDENDUM` (its place in the constant too), owner edits and variables kept, as a new
UNLABELLED version. Nothing is published, and the log says so, when the production text no
longer carries `PO_REFS_ADDENDUM` verbatim or still predates the wording layer (a freshly
bootstrapped database, where an edit of it would carry the registry lists as literal text).
Promoting is one label move in the admin UI, after crew's live parser checks; rolling back is the reverse move. Until then the strict output
schema already carries both keys (absent on the old prompt, tolerated), so nothing changes.

Idempotent: skipped when a version already carries the addendum.

Revision ID: maskp_0001_parser_sub_asks
Revises: bflo_0001_borrow_order_toggle
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

revision = "maskp_0001_parser_sub_asks"
down_revision = "bflo_0001_borrow_order_toggle"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
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

    from app.services.chatbot_prompt_vars import literal_lists

    production = session.execute(
        sql(
            "SELECT v.template FROM ai_prompt_versions v JOIN ai_prompt_labels l ON l.version_id = v.id "
            "WHERE l.name = :n AND l.label = 'production'"
        ),
        {"n": PROMPT_NAME},
    ).scalar()
    if production is not None and set(literal_lists(production)) - {"access_levels"}:
        # A production text from before the wording layer (a freshly bootstrapped database:
        # the wording layer's own version is published unlabelled). An edit of it would
        # carry the registry lists as literal text, which the drift test refuses.
        logger.warning(
            "chatbot parser sub asks prompt NOT published: production predates the wording "
            "layer; promote the wording layer version first, then re-run this publish"
        )
        return None
    version = publish_wording_edit(
        session, old=PO_REFS_ADDENDUM, new=PO_REFS_ADDENDUM + SUB_ASKS_ADDENDUM, message=MESSAGE
    )
    if version is None:
        logger.warning(
            "chatbot parser sub asks prompt NOT published: the production text does not carry "
            "PO_REFS_ADDENDUM verbatim; publish it by hand from the Prompts page"
        )
        return None
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
