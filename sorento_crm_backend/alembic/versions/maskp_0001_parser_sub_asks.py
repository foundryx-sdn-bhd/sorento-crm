"""Publish the chatbot parser prompt with the SUB ASKS and FAMILY keys as a NEW version,
label unmoved (MULTI-ASK-PLAN S2, documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md).

`app/services/chatbot_parser_prompt.SUB_ASKS_ADDENDUM` defines `sub_asks` (every thing a
message asks, in order, when it asks two or more) and `family` (a typed code means the exact
code or its whole family). The engine's per-ask runner (`turn/sub_asks.py`) answers a
two-or-more-ask message from them.

MULTI-ASK-PLAN2 (owner, 12 Oct 2026): built on main's trimmed parser base, the PROMPT-ABLATION
text `pab_0001_parser_ablation` publishes (`alembic/data/chatbot_semantic_parser.ablation-20261011.txt`,
dev v56), never on the old production text. The new version is that text with the addendum
right before its MEMORY block (the tail), published verbatim with no policy blocks, as
`pab_0001` does. Promoting is one label move in the Chatbot Console after crew's live parser
checks; rolling back is the reverse move. Until then the strict output schema already
carries both keys (absent on the old prompt, tolerated), so nothing changes.

Idempotent: a version already carrying this exact template publishes nothing.

Revision ID: maskp_0001_parser_sub_asks
Revises: family_code_0001
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "maskp_0001_parser_sub_asks"
down_revision = "family_code_0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
BASE = Path(__file__).resolve().parents[1] / "data" / "chatbot_semantic_parser.ablation-20261011.txt"
#: The head of MEMORY_ADDENDUM, the base text's tail block.
MEMORY_ANCHOR = "\n\n== MEMORY ==\n"
MESSAGE = (
    "MULTI-ASK-PLAN2: the sub_asks and family keys on the PROMPT-ABLATION base "
    "(one entry per thing a message asks; exact code or family). Unlabelled, promote from the Chatbot Console."
)


def template() -> str:
    from app.services.chatbot_parser_prompt import SUB_ASKS_ADDENDUM

    base = BASE.read_text(encoding="utf-8")
    if base.count(MEMORY_ANCHOR) != 1:
        raise RuntimeError("maskp_0001: the ablation base has no single MEMORY block to put the addendum before")
    return base.replace(MEMORY_ANCHOR, SUB_ASKS_ADDENDUM + MEMORY_ANCHOR)


def publish(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion

    text = template()
    existing = (
        session.query(AIPromptVersion.version)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == text)
        .first()
    )
    if existing is not None:
        logger.info("maskp_0001: sub asks prompt already published as v%s", existing[0])
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=text,
            variables=["current_date"],
            config_json=None,
            commit_message=MESSAGE,
        )
    )
    session.flush()
    logger.info("maskp_0001: published chatbot_semantic_parser v%s, label unmoved", next_version)
    return next_version


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
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
    """Drop the unlabelled version this published; a labelled one stays (a downgrade never
    deletes a label)."""
    op.get_bind().execute(
        sa.text(
            "DELETE FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser' "
            "AND commit_message = :m "
            "AND id NOT IN (SELECT version_id FROM ai_prompt_labels)"
        ),
        {"m": MESSAGE},
    )
