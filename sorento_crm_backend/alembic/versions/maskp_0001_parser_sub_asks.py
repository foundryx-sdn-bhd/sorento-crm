"""Publish the chatbot parser prompt with the SUB ASKS and FAMILY keys as a NEW version,
label unmoved (MULTI-ASK-PLAN S2, documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md).

`app/services/chatbot_parser_prompt.SUB_ASKS_ADDENDUM` (frozen for this revision in
`alembic/data/chatbot_semantic_parser.sub-asks-addendum-20261012.txt`) defines `sub_asks` (every thing a
message asks, in order, when it asks two or more) and `family` (a typed code means the exact
code or its whole family). The engine's per-ask runner (`turn/sub_asks.py`) answers a
two-or-more-ask message from them.

MULTI-ASK-PLAN2 (owner, 12 Oct 2026): built on main's trimmed parser base, never on the old
production text. Main carries two unlabelled v56 candidates, neither holding the other:
`rkind_0002_parser_ref_kinds_v56` (v56 plus CHAT_REPORT and REF_KINDS,
`alembic/data/chatbot_semantic_parser.refkinds-v56-20261011.txt`) and `devf_0001_parser_data_rules`
(v56 plus the DEV-FAILURES and portal_form addenda). This version is the rkind_0002 text with
the sub asks addendum right before its MEMORY block, then the two DEV-FAILURES addenda appended
exactly as `devf_0001` appends them, so one label move promotes all of them. Published
verbatim with no policy blocks. Promoting is one label move in the Chatbot Console after
crew's live parser checks; rolling back is the reverse move.

Idempotent: a version already carrying this exact template publishes nothing.

Revision ID: maskp_0001_parser_sub_asks
Revises: scope_seeds_debtor_company
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "maskp_0001_parser_sub_asks"
down_revision = "scope_seeds_debtor_company"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
DATA = Path(__file__).resolve().parents[1] / "data"
BASE = DATA / "chatbot_semantic_parser.refkinds-v56-20261011.txt"
#: SUB_ASKS_ADDENDUM as this revision publishes it, frozen so a later edit of the code
#: constant never changes what this revision writes (a test holds the two equal today).
ADDENDUM = DATA / "chatbot_semantic_parser.sub-asks-addendum-20261012.txt"
#: The DEV-FAILURES addenda, appended after the whole text as `devf_0001_parser_data_rules` does.
DEV_FAILURES_ADDENDA = (
    DATA / "chatbot_semantic_parser.dev-failures.addendum.txt",
    DATA / "chatbot_semantic_parser.portal-form.addendum.txt",
)
#: The head of MEMORY_ADDENDUM, the base text's tail block.
MEMORY_ANCHOR = "\n\n== MEMORY ==\n"
#: The rendered policy blocks a production text without the MEMORY block ends with
#: (`chatbot_parser_prompt.BLOCKS_BEGIN`).
BLOCKS_ANCHOR = "\n\n<<<CHATBOT POLICY BLOCKS>>>"
#: The production-line version (crew live check 13 Oct 2026: the version crew pins is prod's
#: own line, v52 on dev and prod): the production text plus the addendum.
PROD_MESSAGE = (
    "MULTI-ASK-PLAN2 (production line): the production parser text plus the sub_asks and "
    "family keys. Unlabelled, promote from the Chatbot Console."
)
MESSAGE = (
    "MULTI-ASK-PLAN2: the sub_asks and family keys on the v56 REF-KINDS base plus DEV-FAILURES "
    "(one entry per thing a message asks; exact code or family). Unlabelled, promote from the Chatbot Console."
)


def template() -> str:
    base = BASE.read_text(encoding="utf-8")
    if base.count(MEMORY_ANCHOR) != 1:
        raise RuntimeError("maskp_0001: the ablation base has no single MEMORY block to put the addendum before")
    text = base.replace(MEMORY_ANCHOR, ADDENDUM.read_text(encoding="utf-8") + MEMORY_ANCHOR)
    return text.rstrip("\n") + "".join(a.read_text(encoding="utf-8") for a in DEV_FAILURES_ADDENDA)


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


def publish_on_production(session: Session) -> int | None:
    """The production-labelled text (prod's own line) with the addendum right before its
    MEMORY block, else right before its policy blocks, as a new UNLABELLED version. None on
    a database with no production label (a fresh one) or when this text is already
    published; a production text with neither anchor fails loudly."""
    from app.models.ai_prompt import AIPromptVersion

    row = session.execute(
        sa.text(
            "SELECT v.template, v.variables FROM ai_prompt_versions v JOIN ai_prompt_labels l "
            "ON l.version_id = v.id WHERE l.name = :n AND l.label = 'production'"
        ),
        {"n": PROMPT_NAME},
    ).first()
    if row is None:
        logger.warning("maskp_0001: no production-labelled parser version, production line not published")
        return None
    production = row[0]
    addendum = ADDENDUM.read_text(encoding="utf-8")
    if addendum in production:
        logger.info("maskp_0001: the production text already carries the sub asks addendum")
        return None
    anchor = MEMORY_ANCHOR if MEMORY_ANCHOR in production else BLOCKS_ANCHOR if BLOCKS_ANCHOR in production else None
    if anchor is None:
        raise RuntimeError(
            "maskp_0001: the production parser text carries neither the MEMORY block nor the policy "
            "blocks, so the sub asks addendum has no place to go; publish it by hand"
        )
    template = production.replace(anchor, addendum + anchor, 1)
    existing = (
        session.query(AIPromptVersion.version)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("maskp_0001: production line already published as v%s", existing[0])
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=list(row[1] or ["current_date"]),
            config_json={"wording_edit": True},
            commit_message=PROD_MESSAGE,
        )
    )
    session.flush()
    logger.info("maskp_0001: published the production line as v%s, label unmoved", next_version)
    return next_version


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
    seed_prompt_registry(bind)
    session = Session(bind=bind)
    try:
        publish(session)
        publish_on_production(session)
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
            "AND commit_message IN (:m, :p) "
            "AND id NOT IN (SELECT version_id FROM ai_prompt_labels)"
        ),
        {"m": MESSAGE, "p": PROD_MESSAGE},
    )
