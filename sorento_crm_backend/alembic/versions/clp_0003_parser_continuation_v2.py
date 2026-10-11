"""Publish v56 (the PROMPT-ABLATION parser text) plus the AMENDED SAME SUBJECT OR A NEW ONE block
as a NEW chatbot_semantic_parser version, label unmoved (CLARIFY-POLICY, crew ruling 11 Oct 2026:
a message with its own date window or a whole list is not a continuation).

The base is `alembic/data/chatbot_semantic_parser.ablation-20261011.txt`, read VERBATIM the way
`pab_0001_parser_ablation` reads it (the owner adopted v56 as the base, #1574). The block,
`app/services/chatbot_parser_prompt.CONTINUATION_ADDENDUM`, is inserted as its own section right
before `== MEMORY ==` (how #1574's PR body re-applies another lane's new section onto v56), else
before the CURRENT DATE tail, else at the end. Never built from production, the newest version
or the code constant. `clp_0002_parser_continuation` (the first wording) is left as it is.

Idempotent on the exact template; `production` never moves; the downgrade removes only the
unlabelled row this published.

Revision ID: clp_0003_parser_continuation_v2
Revises: clp_0002_parser_continuation
"""
import logging
from pathlib import Path

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

#: v56, the base this revision builds on (the same file `pab_0001_parser_ablation` publishes).
SOURCE = Path(__file__).resolve().parents[1] / "data" / "chatbot_semantic_parser.ablation-20261011.txt"

#: The block's own heading: a text carrying it already has a continuation block.
BLOCK_HEADING = "\nSAME SUBJECT OR A NEW ONE\n"

_MEMORY = "== MEMORY =="
_CURRENT_DATE_TAIL = "\n\n\n" + "\u2501" * 35 + "\nCURRENT DATE\n"


def with_amended_block(text: str) -> str:
    """`text` carrying the amended block once, as its own section before `== MEMORY ==`
    (else before the CURRENT DATE tail, else at the end)."""
    from app.services.chatbot_parser_prompt import CONTINUATION_ADDENDUM

    block = CONTINUATION_ADDENDUM.strip("\n")
    if BLOCK_HEADING in text:
        return text
    if _MEMORY in text:
        head, rest = text.split(_MEMORY, 1)
        return f"{head}{block}\n\n\n{_MEMORY}{rest}"
    if _CURRENT_DATE_TAIL in text:
        head, rest = text.split(_CURRENT_DATE_TAIL, 1)
        return f"{head}\n\n{block}{_CURRENT_DATE_TAIL}{rest}"
    return f"{text.rstrip(chr(10))}\n\n{block}\n"


def publish(session: Session) -> int | None:
    """Publish v56 + the amended block as the next `chatbot_semantic_parser` version. Returns the
    new version number, or `None` when a version already carries this exact template."""
    template = with_amended_block(SOURCE.read_text(encoding="utf-8"))
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("v56 + amended continuation already published as v%s", existing.version)
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
            config_json={MARKER_KEY: True},
            commit_message=(
                "CLARIFY-POLICY: v56 (PROMPT-ABLATION) plus the amended SAME SUBJECT OR A NEW ONE "
                "block. Unlabelled, promote from the Prompts page."
            ),
        )
    )
    session.commit()
    logger.info(
        "published v56 + amended continuation as chatbot_semantic_parser v%s (%s chars); "
        "production label left where it was",
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
