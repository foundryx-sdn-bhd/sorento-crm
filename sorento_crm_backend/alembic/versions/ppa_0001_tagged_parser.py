"""The production parser prompt, tagged per audience (PARSER-PER-AUDIENCE, #1429).

Publishes the owner's text of 1 Oct 2026 with `{{#only <tag>}}` markers around every span
a reveal grant removes (`alembic/data/chatbot_semantic_parser.prod-20261001.tagged.txt`,
the table is `app/services/chatbot/prompt_gates.PROMPT_GATES`) as ONE new UNLABELLED
version. No label moves and no existing version is touched; the owner publishes it
himself, and from then on each contact is sent the render for the grants it holds.

Proven before insert: rendering the tagged text for all four gated grants equals the
owner's file, byte for byte, with the #1432 ACCOUNT-LEDGER block carried the same way
`pdyn_0003` carries it. If the proof fails, nothing is written and the failure is logged.
Lists are literal, as in the owner's file (no registry variables).

Nothing is written without a `production` label (a blank database). Idempotent: does
nothing when a version from the same tagged file exists. Downgrade deletes only its own
row, and only while it is unlabelled.

Revision ID: ppa_0001_tagged_parser
Revises: pdyn_0003_prod_identical
Create Date: 2026-10-05
"""
from __future__ import annotations

import hashlib
import logging
import pathlib

from alembic import op
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

revision = "ppa_0001_tagged_parser"
down_revision = "pdyn_0003_prod_identical"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
SOURCE = DATA / "chatbot_semantic_parser.prod-20261001.txt"
TAGGED = DATA / "chatbot_semantic_parser.prod-20261001.tagged.txt"
MESSAGE = (
    "Production text of 1 Oct 2026 tagged per audience: each span a reveal grant removes is "
    "wrapped in {{#only tag}}. Renders identical to the text plus the ACCOUNT-LEDGER block "
    "for a contact holding all four gated grants. Unlabelled: publish from the Prompts page."
)


def _sha() -> str:
    return hashlib.sha256(TAGGED.read_bytes()).hexdigest()


def apply(bind) -> int | None:
    from app.models.ai_prompt import AIPromptVersion
    from app.services import chatbot_prompt_vars
    from app.services.chatbot.prompt_gates import PROMPT_GATES, render_for_audience

    source = SOURCE.read_text(encoding="utf-8")
    tagged = TAGGED.read_text(encoding="utf-8")
    sha = _sha()
    session = Session(bind=bind)
    try:
        live = session.execute(
            sql(
                "SELECT v.version, v.variables FROM ai_prompt_versions v JOIN ai_prompt_labels l "
                "ON l.version_id = v.id WHERE l.name = :n AND l.label = 'production'"
            ),
            {"n": PROMPT_NAME},
        ).first()
        if live is None:
            logger.warning("tagged parser: no production label on %s; nothing written", PROMPT_NAME)
            return None
        template = chatbot_prompt_vars.with_account_block(tagged)
        everything = [gate.grant for gate in PROMPT_GATES]
        if render_for_audience(template, everything) != chatbot_prompt_vars.with_account_block(source):
            logger.warning("tagged parser: the full-audience render differs from the file; nothing written")
            return None
        exists = session.execute(
            sql(
                "SELECT version FROM ai_prompt_versions WHERE name = :n "
                "AND (template = :t OR config_json->>'tagged_sha256' = :s) LIMIT 1"
            ),
            {"n": PROMPT_NAME, "t": template, "s": sha},
        ).scalar()
        if exists is not None:
            logger.info("tagged parser already published as v%s; nothing to do", exists)
            return None
        top = session.execute(
            sql("SELECT max(version) FROM ai_prompt_versions WHERE name = :n"), {"n": PROMPT_NAME}
        ).scalar()
        version = int(top or 0) + 1
        session.add(
            AIPromptVersion(
                name=PROMPT_NAME,
                version=version,
                type="text",
                template=template,
                variables=list(live[1] or ["current_date"]),
                config_json={
                    "tagged_snapshot": TAGGED.name,
                    "tagged_sha256": sha,
                    "from_production_version": int(live[0]),
                    "full_audience_identical": True,
                },
                commit_message=MESSAGE,
            )
        )
        session.commit()
        return version
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def remove(bind) -> None:
    """Delete this migration's own row, and only while nobody has labelled it."""
    bind.execute(
        sql(
            "DELETE FROM ai_prompt_versions v WHERE v.name = :n "
            "AND v.config_json->>'tagged_sha256' = :s "
            "AND NOT EXISTS (SELECT 1 FROM ai_prompt_labels l WHERE l.version_id = v.id)"
        ),
        {"n": PROMPT_NAME, "s": _sha()},
    )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    remove(op.get_bind())
