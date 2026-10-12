"""Publish the SPARSE OUTPUT parser prompt as two NEW unlabelled versions, label unmoved
(CHAT-SPARSE, documentation/plans/chatbot/PLAN-chat-sparse-emission-12oct.md).

`chatbot_parser_prompt.sparse_prompt` turns a parser version's text into one that writes
only the keys carrying a value (the live measurement: completion tokens 396 -> 97, parse
p50 2.52 -> 1.54 s). `head/parser.py` reads the marker the text then carries, calls the
provider non-strict and hydrates the answer back to the full shape.

Two bodies, each published only if no version already carries it:

1. The PRODUCTION line: the text the `production` label points at when this runs (v52 on
   prod), made sparse. Its variables are copied from that version. The label is read,
   never written.
2. The CODE CONSTANT line: `chatbot_rearch_s4`'s formula (the constant plus the rendered
   policy blocks, as `rkind_0001_parser_ref_kinds` builds it), made sparse.

A text that is already sparse, or that does not carry the OUTPUT block exactly once, is
skipped with a log line, never a failed migration: promotion is the owner's call and a
missing candidate is visible on the Prompts page. Promoting is one label move; rolling back
is the reverse move.

Revision ID: chs_0001_parser_sparse_output
Revises: rkind_0003_reference_kind_rows
"""
import importlib.util
import logging
from pathlib import Path

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.chatbot_parser_prompt import sparse_prompt

revision = "chs_0001_parser_sparse_output"
down_revision = "rkind_0003_reference_kind_rows"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"

#: Stamped on each version this publishes, so `downgrade()` removes those rows alone.
MARKER_KEY = "chs_0001_parser_sparse_output"


def _load_s4():
    """`chatbot_rearch_s4`, for its body formula. Importing a revision file runs no DDL."""
    spec = importlib.util.spec_from_file_location(
        "_chs_0001_s4", Path(__file__).resolve().parent / "chatbot_rearch_s4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production(session: Session) -> AIPromptVersion | None:
    return (
        session.query(AIPromptVersion)
        .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def _publish(session: Session, *, source: str, variables: list, line: str, base: str) -> int | None:
    try:
        template = sparse_prompt(source)
    except ValueError as exc:
        logger.info("chs_0001: %s line not published (%s)", line, exc)
        return None
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        logger.info("chs_0001: sparse %s line already published as v%s", line, existing.version)
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=list(variables),
            config_json={MARKER_KEY: line, "built_on": base},
            commit_message=(
                f"CHAT-SPARSE: SPARSE OUTPUT on the {line} line ({base}). "
                "Unlabelled, promote from the Prompts page."
            ),
        )
    )
    session.flush()
    logger.info("chs_0001: published sparse %s line as v%s, label unmoved", line, next_version)
    return next_version


def publish(session: Session) -> list[int]:
    """Publish both sparse lines; returns the version numbers published (none when both
    already exist)."""
    published: list[int] = []
    production = _production(session)
    if production is not None:
        number = _publish(
            session,
            source=production.template,
            variables=production.variables or ["current_date"],
            line="production",
            base=f"v{production.version}",
        )
        if number is not None:
            published.append(number)
    template, _blocks_hash = _load_s4()._body(session)
    number = _publish(
        session,
        source=template,
        variables=list(PROMPT_KEYS[PROMPT_NAME].variables),
        line="code constant",
        base="SEMANTIC_PARSER_PROMPT + policy blocks",
    )
    if number is not None:
        published.append(number)
    return published


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
    """Drop the unlabelled versions this published (by their marker), unless the owner
    has since moved a label onto one (a downgrade must never delete a label)."""
    session = Session(bind=op.get_bind())
    try:
        labelled = {
            row.version_id
            for row in session.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME)
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
