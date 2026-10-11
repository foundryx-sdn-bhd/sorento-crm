"""Publish the chatbot parser prompt with the AMENDED SAME SUBJECT OR A NEW ONE paragraph as a NEW
version, label unmoved (CLARIFY-POLICY, crew ruling 11 Oct 2026 on the live "delivery today"
conflict: a message with its own date window or a whole list is not a continuation).

`clp_0002_parser_continuation` published the first wording and is applied on the crew test copy,
so it is not edited. This revision takes the text `clp_0002` built on (the wording layer:
production when production is one, else the newest wording-layer version; else production, else
the newest version) and REPLACES the first wording, frozen below as `_OLD_BLOCK`, with
`app/services/chatbot_parser_prompt.CONTINUATION_ADDENDUM`; a text with neither gets the amended
block added before the policy blocks, the `clp_0002` way. When the newest parser version already
carries the first wording (`clp_0002`'s own unlabelled version), that is the base, so the owner
promotes ONE version. Never rebuilt from the code constant; `production` never moves.

Idempotent: nothing is published when the base already carries the amended block or a version
already carries the exact template.

Revision ID: clp_0003_parser_continuation_v2
Revises: clp_0002_parser_continuation
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "clp_0003_parser_continuation_v2"
down_revision = "clp_0002_parser_continuation"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"

#: Stamped on the version this publishes, so `downgrade()` removes that row alone.
MARKER_KEY = "clp_0003_parser_continuation_v2"

#: The FIRST wording `clp_0002` published, frozen here so a later edit of the code constant
#: never changes what this revision looks for.
_OLD_BLOCK = """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAME SUBJECT OR A NEW ONE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"continuation" is true when the current message asks about the SAME subject as the
previous turn without naming it again: a follow-up about what was just shown ("and its
price?", "cert?", "any incoming?", "check stock" right after a product answer, "how about
delivery?"). It is false when the message names a subject of its own (a code, a name, a
category), asks for a report or a list that needs no subject ("low stock report", "top
selling items", "my outstanding"), or starts something unrelated. A pick off a numbered
list, "yes" or "no" is false. When continuation is true, entity_op is "reuse"."""


def _new_block() -> str:
    from app.services.chatbot_parser_prompt import CONTINUATION_ADDENDUM

    return CONTINUATION_ADDENDUM.strip("\n")


def with_amended_block(text: str) -> str:
    """`text` carrying the amended paragraph once: the first wording replaced in place, else the
    amended block added just before the policy blocks (at the end when there are none)."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN

    new = _new_block()
    if new in text:
        return text
    if _OLD_BLOCK in text:
        return text.replace(_OLD_BLOCK, new, 1)
    if BLOCKS_BEGIN in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        return f"{head}{new}\n\n{BLOCKS_BEGIN}{rest}"
    return f"{text.rstrip(chr(10))}\n\n{new}\n"


def _production(session: Session) -> AIPromptVersion | None:
    return (
        session.query(AIPromptVersion)
        .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def _base(session: Session) -> AIPromptVersion | None:
    """The newest version carrying the first wording (`clp_0002`'s), else the text `clp_0002`
    would have built on. Never the code constant."""
    from app.services.chatbot_prompt_vars import is_wording_layer

    rows = list(
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .order_by(AIPromptVersion.version.desc())
    )
    carrying = next((row for row in rows if _OLD_BLOCK in (row.template or "")), None)
    if carrying is not None:
        return carrying
    production = _production(session)
    if production is not None and is_wording_layer(production.template):
        return production
    wording = next((row for row in rows if is_wording_layer(row.template)), None)
    return wording or production or (rows[0] if rows else None)


def publish(session: Session) -> int | None:
    """Publish the amended prompt as the next `chatbot_semantic_parser` version. Returns the new
    version number, or `None` when there is nothing to publish."""
    base = _base(session)
    if base is None:
        logger.info("no chatbot parser prompt version to amend")
        return None
    template = with_amended_block(base.template)
    if template == base.template:
        logger.info("parser prompt v%s already carries the amended continuation paragraph", base.version)
        return None
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("amended continuation prompt already published as v%s", existing.version)
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=list(base.variables or []),
            config_json={"from_version": int(base.version), MARKER_KEY: True},
            commit_message=(
                "CLARIFY-POLICY: continuation amended (own date window or whole list is not a "
                "continuation). Unlabelled, promote from the Prompts page."
            ),
        )
    )
    session.commit()
    logger.info(
        "published amended chatbot parser continuation prompt as v%s (%s chars); production "
        "label left on the previous version, promote by moving it",
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
    """Drop the unlabelled version this published (by its marker), unless a label has since
    moved onto it (a downgrade must never delete a label)."""
    session = Session(bind=op.get_bind())
    try:
        labelled = {
            row.version_id
            for row in session.query(AIPromptLabel)
            .join(AIPromptVersion, AIPromptVersion.id == AIPromptLabel.version_id)
            .filter(AIPromptVersion.name == PROMPT_NAME)
        }
        for row in session.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME):
            if row.id not in labelled and (row.config_json or {}).get(MARKER_KEY):
                session.delete(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
