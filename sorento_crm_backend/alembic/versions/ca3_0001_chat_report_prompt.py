"""Publish the chatbot parser prompt with the chat report reading as a NEW version, label
unmoved (CA-3, `documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md`).

Same pattern as `report_drill_0001_prompt`: `ai_prompt_registry.render()` reads the PUBLISHED
row, not the Python constant, so the chat report vocabulary (the `chat_report` object, the
`chat_analytics` domain, the `chat_report` intent) reaches a live turn only once the owner
moves the `production` label, or hand-merges the block into the prod prompt the owner already
edited by hand. The insert helper is repeated, not imported: a migration module must not
depend on another migration's module staying importable.

Revision ID: ca3_0001_chat_report_prompt
Revises: chat_topics_0002_audit
"""
import logging

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

revision = "ca3_0001_chat_report_prompt"
down_revision = "chat_topics_0002_audit"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"


MARKER = "CA-3 chat report: the chat_report object and the chat_analytics domain"
#: The production text is edited at its MEMORY section, the tail the addenda sit above.
ANCHOR = "\n\n== MEMORY =="


def _addendum() -> str:
    from app.services.chatbot_parser_prompt import CHAT_REPORT_ADDENDUM

    return CHAT_REPORT_ADDENDUM


def publish(session: Session) -> int | None:
    """Publish the production text plus the chat report block as the next version, label
    unmoved (the shape of `chatbot_prompt_vars.publish_wording_edit`: a later version changes the owner's
    wording by one exact edit, never by republishing the code constant, which would put the
    registry lists back as literal text). Returns the new version number, or None when this
    edit is already published or the production text has no MEMORY section to anchor on."""
    done = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.commit_message == MARKER)
        .first()
    )
    if done is not None:
        logger.info("chat report prompt edit already published as v%s", done.version)
        return None
    # The text to edit: production when it is the wording layer (registry variables, the
    # owner's wording), else the newest version that is (a database whose owner has not
    # promoted it yet), else production as it stands. Never a literal-list republish.
    from app.services.chatbot_prompt_vars import is_wording_layer

    production = session.execute(
        text(
            "SELECT v.template, v.variables FROM ai_prompt_versions v JOIN ai_prompt_labels l "
            "ON l.version_id = v.id WHERE l.name = :n AND l.label = 'production'"
        ),
        {"n": PROMPT_NAME},
    ).first()
    if production is None:
        logger.warning("chat report prompt edit not published: no production text")
        return None
    base = production
    if not is_wording_layer(production[0]):
        newest = [
            r for r in session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_NAME).order_by(AIPromptVersion.version.desc())
            if is_wording_layer(r.template)
        ]
        if newest:
            base = (newest[0].template, newest[0].variables)
    # The block goes above the MEMORY section; a text without one takes it at the end.
    block = _addendum().rstrip("\n")
    template = (
        base[0].replace(ANCHOR, block + ANCHOR, 1)
        if ANCHOR in base[0]
        else base[0].rstrip("\n") + "\n\n" + block
    )
    top = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    version = max((int(v[0]) for v in top), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=version,
            type="text",
            template=template,
            variables=list(base[1] or []),
            config_json={"wording_edit": True},
            commit_message=MARKER,
        )
    )
    session.commit()
    logger.info("published chat report prompt edit as v%s; production label left where it was", version)
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
    """Drop the version this migration published, unless a label now points at it."""
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
            AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.commit_message == MARKER
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
