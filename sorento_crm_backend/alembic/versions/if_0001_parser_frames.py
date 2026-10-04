"""Publish the chatbot parser prompt with SAME SUBJECT OR A NEW ONE as a NEW version, label
unmoved (INTENT-FRAMES, documentation/plans/chatbot/PLAN-intent-frames-4oct.md).

`app/services/chatbot_parser_prompt.INTENT_FRAMES_ADDENDUM` defines the `continuation` key
(the schema carried it undefined) as the parser's own "same subject" signal, which the
engine reads to decide what moves into a new intent's frame, and adds "clear", "reset",
"start over" to TOPIC RESET (owner ruling Q4, 4 Oct 2026).

Same shape as `lss_0001_parser_vocab`: the body is `chatbot_rearch_s4`'s formula, landing
as the next `chatbot_semantic_parser` version with NO label move. Promoting is one label
move in the admin UI; rolling back is the reverse move. Additive data only.

Once the owner's wording layer exists (PROMPT-DYNAMIC #1405, `pdyn_0002` / `pdyn_0003`)
the code constant is no longer the source of the wording, so the block is instead added
once to the wording-layer text (production when production is one, else the newest
wording-layer version), just before the policy blocks, the way
`chatbot_prompt_vars.with_account_block` adds the ACCOUNT-LEDGER block. Owner edits and
registry variables are kept.

Idempotent, and safe on a fresh database: `seed_prompt_registry` runs first, and the
publish is skipped when a version already carries this exact template (or, on the
wording layer, when the base text already has the block).

Revision ID: if_0001_parser_frames
Revises: pdyn_0003_prod_identical
"""
import importlib.util
import logging
from pathlib import Path

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS

revision = "if_0001_parser_frames"
down_revision = "pdyn_0003_prod_identical"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"

#: Stamped on the version this publishes, so `downgrade()` removes that row alone.
MARKER_KEY = "if_0001_parser_frames"

#: The block's own heading: a text carrying it already has the block.
BLOCK_HEADING = "\nSAME SUBJECT OR A NEW ONE\n"


def _load_s4():
    """`chatbot_rearch_s4`, for its body formula (the same load `mem_0002_parser_memory`
    does). Importing a revision file runs no DDL."""
    spec = importlib.util.spec_from_file_location(
        "_if_0001_s4", Path(__file__).resolve().parent / "chatbot_rearch_s4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def with_frames_block(text: str) -> str:
    """`text` carrying INTENT_FRAMES_ADDENDUM once, just before the policy blocks (at the
    end when there are none). A text that already has it is returned as it is."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, INTENT_FRAMES_ADDENDUM

    if BLOCK_HEADING in text:
        return text
    block = INTENT_FRAMES_ADDENDUM.strip("\n")
    if BLOCKS_BEGIN in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        return f"{head}{block}\n\n{BLOCKS_BEGIN}{rest}"
    return f"{text.rstrip(chr(10))}\n\n{block}\n"


def _wording_layer_base(session: Session) -> AIPromptVersion | None:
    """The text the owner's wording lives in: production when it is a wording-layer
    version, else the newest wording-layer version (`pdyn_0003` / `pdyn_0002` publish
    theirs unlabelled). None while no wording layer exists."""
    from app.services.chatbot_prompt_vars import is_wording_layer

    production = (
        session.query(AIPromptVersion)
        .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )
    if production is not None and is_wording_layer(production.template):
        return production
    rows = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .order_by(AIPromptVersion.version.desc())
    )
    return next((row for row in rows if is_wording_layer(row.template)), None)


def publish(session: Session) -> int | None:
    """Publish the intent-frames prompt as the next `chatbot_semantic_parser`
    version, unless a version already carries this exact template. Returns the new
    version number, or `None` when already published."""
    base = _wording_layer_base(session)
    if base is not None:
        template = with_frames_block(base.template)
        if template == base.template:
            logger.info("parser wording layer v%s already carries SAME SUBJECT OR A NEW ONE", base.version)
            return None
        variables = list(base.variables or [])
        config = {"from_version": int(base.version), MARKER_KEY: True}
    else:
        template, blocks_hash = _load_s4()._body(session)
        variables = list(PROMPT_KEYS[PROMPT_NAME].variables)
        config = {"blocks_hash": blocks_hash, MARKER_KEY: True}
    existing = (
        session.query(AIPromptVersion)
        .filter(
            AIPromptVersion.name == PROMPT_NAME,
            AIPromptVersion.template == template,
        )
        .first()
    )
    if existing is not None:
        logger.info(
            "chatbot parser intent frames prompt already published as v%s; nothing to do",
            existing.version,
        )
        return None
    versions = (
        session.query(AIPromptVersion.version)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .all()
    )
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=variables,
            config_json=config,
            commit_message=(
                "INTENT-FRAMES: continuation defined as the same-subject signal; "
                "clear / reset / start over are a topic reset. "
                "Unlabelled, promote from the Prompts page."
            ),
        )
    )
    session.commit()
    logger.info(
        "published chatbot parser intent frames prompt as v%s (%s chars); production "
        "label left on the previous version, promote by moving it",
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
    """Drop the unlabelled version this published (by its marker), unless the owner
    has since moved a label onto it (a label row cascades with its version, and a
    downgrade must never delete a label)."""
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
