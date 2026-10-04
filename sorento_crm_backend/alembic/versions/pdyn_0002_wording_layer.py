"""The parser prompt's wording layer (PLAN-prompt-dynamic-30sep D7, R1).

Reads the text the `production` label points at (on prod: v42 with the owner's edits),
replaces every hand-copied registry list with its `{{variable}}` via
`chatbot_prompt_vars.wording_layer`, and inserts the result as a NEW UNLABELLED version.
Nothing else is rewritten: the owner's wording stays verbatim, a list his registry does
not fully cover stays literal, and the report of what was replaced is stored on the
version (`config_json.wording_layer_report`) and logged.

The `production` label does NOT move (R3): the owner promotes from the Prompts page after
reading the before/after diff (`scripts/prompt_dynamic_render_diff.py`).

Idempotent: does nothing once any version carries a registry variable. `apply(bind)` is
shared with `scripts.bootstrap_env`.

Revision ID: pdyn_0002_wording_layer
Revises: pdyn_0006_agents_in_prompt
Create Date: 2026-09-30
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

revision = "pdyn_0002_wording_layer"
down_revision = "pdyn_0006_agents_in_prompt"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
MESSAGE = (
    "Registry variables wired in (PROMPT-DYNAMIC): domains, statuses, entity kinds, "
    "teams, agents, access levels and the policy blocks now render from their tables on "
    "every turn. Unlabelled - promote from the Prompts page."
)


def apply(bind) -> int | None:
    from app.models.ai_prompt import AIPromptVersion
    from app.services import chatbot_prompt_vars

    session = Session(bind=bind)
    try:
        if chatbot_prompt_vars.wording_layer_exists(session):
            logger.info("parser wording layer already published; nothing to do")
            return None
        row = session.execute(
            sql(
                "SELECT v.version, v.template, v.variables FROM ai_prompt_versions v "
                "JOIN ai_prompt_labels l ON l.version_id = v.id "
                "WHERE l.name = :n AND l.label = 'production'"
            ),
            {"n": PROMPT_NAME},
        ).first()
        if row is None:
            logger.warning("no production %s version; wording layer not published", PROMPT_NAME)
            return None
        template, report = chatbot_prompt_vars.wording_layer(row[1], session)
        # #1432 ACCOUNT-LEDGER merged first: the version carries its `account` block.
        template = chatbot_prompt_vars.with_account_block(template)
        top = session.execute(
            sql("SELECT max(version) FROM ai_prompt_versions WHERE name = :n"), {"n": PROMPT_NAME}
        ).scalar()
        version = int(top) + 1
        session.add(
            AIPromptVersion(
                name=PROMPT_NAME,
                version=version,
                type="text",
                template=template,
                variables=list(row[2] or []),
                config_json={"wording_layer_report": report, "from_version": int(row[0])},
                commit_message=MESSAGE,
            )
        )
        session.commit()
        for line in report:
            logger.info("parser wording layer v%s: %s", version, line)
        return version
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    """Drop the unlabelled wording-layer version. A labelled one is never touched."""
    op.get_bind().execute(
        sql(
            "DELETE FROM ai_prompt_versions v WHERE v.name = :n "
            "AND v.config_json ? 'wording_layer_report' "
            "AND NOT EXISTS (SELECT 1 FROM ai_prompt_labels l WHERE l.version_id = v.id)"
        ),
        {"n": PROMPT_NAME},
    )
