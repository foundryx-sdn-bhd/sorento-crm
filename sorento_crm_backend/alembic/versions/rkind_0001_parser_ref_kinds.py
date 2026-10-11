"""Publish the chatbot parser prompt with the REFERENCE KINDS section as a NEW version,
label unmoved (REF-PROBES, documentation/plans/chatbot/PLAN-ref-probes-11oct.md).

`app/services/chatbot_parser_prompt.REF_KINDS_ADDENDUM` teaches the entity hints
delivery_order, sales_order, customer_po_ref, purchase_order and purchase_request (the kind a
typed reference number was NAMED as). The resolver probes the data for the kind.

Same shape as `mem_0002_parser_memory`: the body is `chatbot_rearch_s4`'s formula (the
constant plus the policy blocks rendered from `chatbot_domains` / `chatbot_entity_kinds`),
landing as the next `chatbot_semantic_parser` version with NO label move. Promoting is one
label move in the admin UI; rolling back is the reverse move. Until then the old
prompt hints a typed number "order" and the resolver still reads its kind from the data:
merge and promote together.

Idempotent, and safe on a fresh database: `seed_prompt_registry` runs first, and the
publish is skipped when a version already carries this exact template.

Revision ID: rkind_0001_parser_ref_kinds
Revises: ca3_0003_chat_report_extractor
"""
import importlib.util
import logging
from pathlib import Path

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS

revision = "rkind_0001_parser_ref_kinds"
down_revision = "ca3_0003_chat_report_extractor"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"

#: Stamped on the version this publishes, so `downgrade()` removes that row alone.
MARKER_KEY = "rkind_0001_parser_ref_kinds"


def _load_s4():
    """`chatbot_rearch_s4`, for its body formula (the same load `mem_0002_parser_memory`
    does). Importing a revision file runs no DDL."""
    spec = importlib.util.spec_from_file_location(
        "_rkind_0001_s4", Path(__file__).resolve().parent / "chatbot_rearch_s4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish(session: Session) -> int | None:
    """Publish the reference kinds prompt as the next `chatbot_semantic_parser`
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
            "chatbot parser reference kinds prompt already published as v%s; nothing to do",
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
                "REF-PROBES: the reference kind hints (delivery_order, sales_order, "
                "customer_po_ref, purchase_order, purchase_request). "
                "Unlabelled, promote from the Prompts page."
            ),
        )
    )
    session.commit()
    logger.info(
        "published chatbot parser reference kinds prompt as v%s (%s chars); production "
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
