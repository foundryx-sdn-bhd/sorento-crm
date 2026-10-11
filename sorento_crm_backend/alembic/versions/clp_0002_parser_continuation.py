"""Publish the chatbot parser prompt with SAME SUBJECT OR A NEW ONE as a NEW version, label
unmoved (CLARIFY-POLICY S5, owner ruling Q10, documentation/plans/chatbot/PLAN-clarify-policy-11oct.md).

The block (`CONTINUATION_ADDENDUM` below, frozen at its first wording; the code constant of the
same name has since been amended, see `clp_0003_parser_continuation_v2`) defines the
`continuation` key (the schema carried it undefined) as the parser's own "same subject" signal,
word for word from INTENT-FRAMES (#1473); the carried-subject rule reads it
(`clarify_policy.carry_conflict`).

The mechanism is #1473's `if_0001_parser_frames`: the block is added once to the
wording-layer text (PROMPT-DYNAMIC #1405: production when production is one, else the newest
wording-layer version), just before the policy blocks. It is never rebuilt from the code
constant: with no wording layer yet, the text it is added to is the current production
version's (else the newest version's). Owner edits and registry variables are kept. The new
version lands with NO label move; promoting it is one label move on the Prompts page.

Idempotent: the publish is skipped when the base text already has the block or a version
already carries this exact template.

Revision ID: clp_0002_parser_continuation
Revises: clp_0001_clarify_policy
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "clp_0002_parser_continuation"
down_revision = "clp_0001_clarify_policy"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"

#: Stamped on the version this publishes, so `downgrade()` removes that row alone.
MARKER_KEY = "clp_0002_parser_continuation"

#: The block's own heading: a text carrying it already has the block.
BLOCK_HEADING = "\nSAME SUBJECT OR A NEW ONE\n"

#: The block this revision publishes, frozen here: the first wording (CLARIFY-POLICY S5, 3d96ef91).
#: A later edit to the code constant `CONTINUATION_ADDENDUM` must never change what an applied
#: revision publishes.
CONTINUATION_ADDENDUM = (
    '\n'
    '\n'
    '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
    'SAME SUBJECT OR A NEW ONE\n'
    '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
    '\n'
    '"continuation" is true when the current message asks about the SAME subject as the\n'
    'previous turn without naming it again: a follow-up about what was just shown ("and its\n'
    'price?", "cert?", "any incoming?", "check stock" right after a product answer, "how about\n'
    'delivery?"). It is false when the message names a subject of its own (a code, a name, a\n'
    'category), asks for a report or a list that needs no subject ("low stock report", "top\n'
    'selling items", "my outstanding"), or starts something unrelated. A pick off a numbered\n'
    'list, "yes" or "no" is false. When continuation is true, entity_op is "reuse".\n'
)


def with_continuation_block(text: str) -> str:
    """`text` carrying CONTINUATION_ADDENDUM once, just before the policy blocks (at the end
    when there are none). A text that already has it is returned as it is."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN

    if BLOCK_HEADING in text:
        return text
    block = CONTINUATION_ADDENDUM.strip("\n")
    if BLOCKS_BEGIN in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        return f"{head}{block}\n\n{BLOCKS_BEGIN}{rest}"
    return f"{text.rstrip(chr(10))}\n\n{block}\n"


def _production(session: Session) -> AIPromptVersion | None:
    return (
        session.query(AIPromptVersion)
        .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def _base(session: Session) -> AIPromptVersion | None:
    """The text the block is added to: the wording layer (production when it is a
    wording-layer version, else the newest wording-layer version), else production, else
    the newest version. Never the code constant."""
    from app.services.chatbot_prompt_vars import is_wording_layer

    production = _production(session)
    if production is not None and is_wording_layer(production.template):
        return production
    rows = list(
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .order_by(AIPromptVersion.version.desc())
    )
    wording = next((row for row in rows if is_wording_layer(row.template)), None)
    return wording or production or (rows[0] if rows else None)


def publish(session: Session) -> int | None:
    """Publish the continuation prompt as the next `chatbot_semantic_parser` version.
    Returns the new version number, or `None` when there is nothing to publish."""
    base = _base(session)
    if base is None:
        logger.info("no chatbot parser prompt version to add SAME SUBJECT OR A NEW ONE to")
        return None
    template = with_continuation_block(base.template)
    if template == base.template:
        logger.info("parser prompt v%s already carries SAME SUBJECT OR A NEW ONE", base.version)
        return None
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("chatbot parser continuation prompt already published as v%s", existing.version)
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
                "CLARIFY-POLICY: continuation defined as the same-subject follow-up signal. "
                "Unlabelled, promote from the Prompts page."
            ),
        )
    )
    session.commit()
    logger.info(
        "published chatbot parser continuation prompt as v%s (%s chars); production label "
        "left on the previous version, promote by moving it",
        next_version,
        len(template),
    )
    return next_version


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
    # v1 from the fallback plus the production label, if absent.
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
    """Drop the unlabelled version this published (by its marker), unless the owner has
    since moved a label onto it (a downgrade must never delete a label)."""
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
