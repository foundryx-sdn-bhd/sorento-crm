"""Publish the chatbot parser prompt with the PO REFS key as a NEW version,
label unmoved (SO-PO-REF, documentation/plans/scm/PLAN-so-po-ref.md).

`app/services/chatbot_parser_prompt.PO_REFS_ADDENDUM` teaches the `po_refs` output key (the
PO number(s) a message gives, typed without the word PO). The business lane answers it for
a dealer/customer only, from `sales_orders.po_ref_key`; staff keep the supplier PO path.

Same shape as `mem_0002_parser_memory`: the body is `chatbot_rearch_s4`'s formula (the
constant plus the policy blocks rendered from `chatbot_domains` / `chatbot_entity_kinds`),
landing as the next `chatbot_semantic_parser` version with NO label move. Promoting is one
label move in the admin UI; rolling back is the reverse move. Until then the strict output
schema already carries `po_refs` (absent on the old prompt, tolerated), so a dealer's PO
ask answers only after the label moves: merge and promote together.

Idempotent, and safe on a fresh database: `seed_prompt_registry` runs first, and the
publish is skipped when a version already carries this exact template.

Revision ID: sopr_0002_parser_po_refs
Revises: sopr_0001_so_po_ref
"""
import importlib.util
import logging
from pathlib import Path

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS

revision = "sopr_0002_parser_po_refs"
down_revision = "sopr_0001_so_po_ref"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"

#: Stamped on the version this publishes, so `downgrade()` removes that row alone.
MARKER_KEY = "sopr_0002_parser_po_refs"


def _load_s4():
    """`chatbot_rearch_s4`, for its body formula (the same load `mem_0002_parser_memory`
    does). Importing a revision file runs no DDL."""
    spec = importlib.util.spec_from_file_location(
        "_lss_0001_s4", Path(__file__).resolve().parent / "chatbot_rearch_s4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish(session: Session) -> int | None:
    """Publish the low-stock-filters prompt as the next `chatbot_semantic_parser`
    version, unless a version already carries this exact template. Returns the new
    version number, or `None` when already published."""
    template, blocks_hash = _load_s4()._body(session)
    spec = PROMPT_KEYS[PROMPT_NAME]
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
            "chatbot parser PO refs prompt already published as v%s; nothing to do",
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
            variables=list(spec.variables),
            config_json={"blocks_hash": blocks_hash, MARKER_KEY: True},
            commit_message=(
                "SO-PO-REF: the po_refs key (a customer's PO number, typed without the "
                "word PO). "
                "Unlabelled, promote from the Prompts page."
            ),
        )
    )
    session.commit()
    logger.info(
        "published chatbot parser PO refs prompt as v%s (%s chars); production "
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
