"""Publish the chatbot parser prompt with the SUB ASKS and FAMILY keys as a NEW version,
label unmoved (MULTI-ASK-PLAN S2, documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md).

`app/services/chatbot_parser_prompt.SUB_ASKS_ADDENDUM` (frozen for this revision in
`alembic/data/chatbot_semantic_parser.sub-asks-addendum-20261012.txt`) defines `sub_asks` (every thing a
message asks, in order, when it asks two or more) and `family` (a typed code means the exact
code or its whole family). The engine's per-ask runner (`turn/sub_asks.py`) answers a
two-or-more-ask message from them.

MULTI-ASK-PLAN2 (owner, 12 Oct 2026): built on main's trimmed parser base, never on the old
production text: the v56 PROMPT-ABLATION text as `rkind_0002_parser_ref_kinds_v56` publishes it
(`alembic/data/chatbot_semantic_parser.refkinds-v56-20261011.txt`: v56 plus CHAT_REPORT and
REF_KINDS), so one label move promotes all three. The new version is that text with the
addendum right before its MEMORY block (the tail), published verbatim with no policy blocks.
Promoting is one label move in the Chatbot Console after crew's live parser checks; rolling
back is the reverse move. Until then the strict output schema already carries both keys
(absent on the old prompt, tolerated), so nothing changes.

Idempotent: a version already carrying this exact template publishes nothing.

Revision ID: maskp_0001_parser_sub_asks
Revises: rkind_0003_reference_kind_rows
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "maskp_0001_parser_sub_asks"
down_revision = "rkind_0003_reference_kind_rows"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
DATA = Path(__file__).resolve().parents[1] / "data"
BASE = DATA / "chatbot_semantic_parser.refkinds-v56-20261011.txt"
#: SUB_ASKS_ADDENDUM as this revision publishes it, frozen so a later edit of the code
#: constant never changes what this revision writes (a test holds the two equal today).
ADDENDUM = DATA / "chatbot_semantic_parser.sub-asks-addendum-20261012.txt"
#: The head of MEMORY_ADDENDUM, the base text's tail block.
MEMORY_ANCHOR = "\n\n== MEMORY ==\n"
MESSAGE = (
    "MULTI-ASK-PLAN2: the sub_asks and family keys on the v56 REF-KINDS base "
    "(one entry per thing a message asks; exact code or family). Unlabelled, promote from the Chatbot Console."
)


def template() -> str:
    base = BASE.read_text(encoding="utf-8")
    if base.count(MEMORY_ANCHOR) != 1:
        raise RuntimeError("maskp_0001: the ablation base has no single MEMORY block to put the addendum before")
    return base.replace(MEMORY_ANCHOR, ADDENDUM.read_text(encoding="utf-8") + MEMORY_ANCHOR)


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
