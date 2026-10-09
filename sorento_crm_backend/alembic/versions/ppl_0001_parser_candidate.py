"""Publish the PROMPT-PROD-LABEL candidate chatbot parser version, label unmoved
(documentation/plans/chatbot/PLAN-prompt-prod-label-10oct.md).

Why: the production label sits on a version (v52) that lacks the ORDER-BROADEN and
rogue-conversation addenda (#1495, #1506), while the owner has since edited the live text in
the console (installation guide, 6 Oct; "warehouse" wording, 1 Oct). A version built from the
code constant alone has none of the owner's edits and no policy blocks. This version is the
constant plus both addenda plus the owner's edits (`alembic/data/chatbot_semantic_parser.
candidate-20261010.txt`), with the policy blocks rendered from the database appended the way
`chatbot_rearch_s4` does.

The production label is never moved and `ai_prompt_labels` is never touched: promoting is one
label move in the Chatbot Console. To rebuild the data file run
`venv/bin/python -m scripts.build_parser_candidate` from sorento_crm_backend/.

Idempotent: a version already carrying this exact template publishes nothing, and a candidate
missing any required marker publishes nothing.

Revision ID: ppl_0001_parser_candidate
Revises: chat_topics_0002_audit
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "ppl_0001_parser_candidate"
down_revision = "chat_topics_0002_audit"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
CANDIDATE = Path(__file__).resolve().parents[1] / "data" / "chatbot_semantic_parser.candidate-20261010.txt"
MESSAGE = "ppl_0001_parser_candidate: constant + order-broaden + rogue-convo addenda + owner console edits, unlabelled"
#: What this version exists to carry; the data file must hold every one.
MARKERS = (
    "CUSTOMER ACCOUNT NUMBER",
    "LOW STOCK REPORT FILTERS",
    "TRAP SPECS",
    "PRODUCT CODE: entities[].canonical_code",
    '"po_refs"',
    "sales_ranking",
    "ranking_drill",
    "== PROMOTION KIND ==",
    "A MISS reply never moves the domain either",
    "A COUNT WITH NO CODE BESIDE IT",
    "A DESCRIPTION WITH A CODE INSIDE IT",
)


def missing_markers(text: str) -> list[str]:
    return [marker for marker in MARKERS if marker not in text]


def with_blocks(body: str, blocks: str) -> str:
    """`body` followed by the policy blocks wrapped in the BEGIN/END fences."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    if not body.endswith("\n"):
        body += "\n"
    return f"{body}\n{BLOCKS_BEGIN}\n{blocks}{BLOCKS_END}\n"


def publish(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion
    from app.services.ai_prompt_registry import PROMPT_KEYS
    from app.services.chatbot_parser_prompt import prompt_blocks_hash, render_prompt_blocks

    body = CANDIDATE.read_text(encoding="utf-8")
    missing = missing_markers(body)
    if missing:
        logger.warning("ppl_0001: candidate lacks %s; nothing published", missing)
        return None
    template = with_blocks(body, render_prompt_blocks(session))
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("ppl_0001: candidate already published as v%s", existing.version)
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
            config_json={"blocks_hash": prompt_blocks_hash(session)},
            commit_message=MESSAGE,
        )
    )
    session.flush()
    logger.info("ppl_0001: published chatbot_semantic_parser v%s, label unmoved", next_version)
    return next_version


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
    # v1 from the fallback plus the production label, if this install never seeded the key.
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
    op.get_bind().execute(
        sa.text(
            "DELETE FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser' "
            "AND commit_message = :m "
            "AND id NOT IN (SELECT version_id FROM ai_prompt_labels)"
        ),
        {"m": MESSAGE},
    )
