"""Publish the first re-architecture parser version, with the policy blocks in its body.

Chatbot turn re-architecture S4 (AC-1550). One NEW version of
``chatbot_semantic_parser``, UNLABELLED, whose body is the ``SEMANTIC_PARSER_PROMPT``
constant with the domain and entity-kind blocks rendered from ``chatbot_domains`` /
``chatbot_entity_kinds`` appended between two markers, and whose ``config_json`` carries
the sha256 of exactly those blocks.

Amended 16 Sep 2026 (AC-1317, D8 "one prompt lineage"): the base used to be whatever
``production`` pointed at today, so a published version could drift from
``SEMANTIC_PARSER_PROMPT`` - the constant itself gained an owner ruling that it is CRM
content now, not a byte-fidelity-locked n8n import, which retired the reason for
preferring the live DB row over it. The constant is now the ONE COPY of the output-keys
declaration (edit it there, not here or in the schema's prose) - this migration derives
its base FROM the constant every time, so publishing can never carry a stale OUTPUT
block forward.

The ``production`` label is NOT moved by THIS migration. That was the whole shape of this
registry and the reason the blocks are rendered at publish time rather than per turn:
deploying this changes nothing about how a live turn is parsed, and promoting it was one
label move the owner made on the Prompts page once they had read the diff. AC-1552's
staleness banner compares ``config_json["blocks_hash"]`` against the rows as they stand
today, so a domain saved after this publish reads as "domain block out of date" instead of
silently rewriting a version somebody already graded.

**Superseded for later publishes by ``chatbot_rearch_s12`` (owner ruling 21 Sep 2026):**
a migration that CHANGES the policy tables now republishes AND moves the label itself, so
a deploy needs no hand setup on prod. See that revision's docstring. This one still leaves
the label alone, because it publishes the tables unchanged.

Idempotent: a version whose FULL rendered template (constant plus policy blocks)
already matches today's is left alone - not just a `blocks_hash` match, since the
constant can change (a new output key, a wording fix) without the policy tables moving
at all.

Revision ID: chatbot_rearch_s4
Revises: chatbot_rearch_s0
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.ai_prompt_seed import seed_prompt_registry
from app.services.chatbot_parser_prompt import (
    BLOCKS_BEGIN,
    BLOCKS_END,
    SEMANTIC_PARSER_PROMPT,
    prompt_blocks_hash,
    render_prompt_blocks,
)

revision = "chatbot_rearch_s4"
down_revision = "chatbot_rearch_s0"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"


def _body(session: Session) -> tuple[str, str]:
    """`(template, blocks_hash)` - the constant plus the rendered policy blocks."""
    blocks = render_prompt_blocks(session)
    template = f"{SEMANTIC_PARSER_PROMPT.rstrip()}\n\n{BLOCKS_BEGIN}\n{blocks}{BLOCKS_END}\n"
    return template, prompt_blocks_hash(session)


def publish_policy_blocks(bind) -> None:
    """Publish the unlabelled version, if the current rendered blocks are not already
    published verbatim.

    Shared by `upgrade()` and `scripts.bootstrap_env.seed_chatbot_policy` - a
    `create_all`-built database has the `ai_prompt_versions` TABLE but never ran this
    migration BODY, so it has no `chatbot_semantic_parser` rows at all until this runs.
    Idempotent (see the full-template equality check below); bootstrap may run twice.
    """
    # v1 plus the production label, if this install has never seeded the key.
    seed_prompt_registry(bind)

    session = Session(bind=bind)
    try:
        # PLAN-prompt-dynamic-30sep R1: once the owner's wording layer exists the blocks
        # render per turn from `{{domains_detail}}` etc., and a version rebuilt from the
        # code constant would drop his edits. Stand down.
        from app.services.chatbot_prompt_vars import wording_layer_exists

        if wording_layer_exists(session):
            logger.info("parser wording layer exists; policy blocks are live, nothing to publish")
            return
        template, blocks_hash = _body(session)
        # `blocks_hash` alone under-counts: it covers the POLICY BLOCKS, not
        # `SEMANTIC_PARSER_PROMPT` itself, so an edit to the constant with the policy
        # tables untouched must still republish. Full-template equality is the correct
        # "nothing would change" check for both.
        already = [
            row
            for row in session.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME)
            if (row.config_json or {}).get("blocks_hash") == blocks_hash and row.template == template
        ]
        if already:
            logger.info(
                "chatbot parser policy blocks already published as v%s; nothing to do",
                already[0].version,
            )
            return

        versions = (
            session.query(AIPromptVersion.version)
            .filter(AIPromptVersion.name == PROMPT_NAME)
            .all()
        )
        next_version = max((int(v[0]) for v in versions), default=0) + 1
        spec = PROMPT_KEYS[PROMPT_NAME]
        session.add(
            AIPromptVersion(
                name=PROMPT_NAME,
                version=next_version,
                type="text",
                template=template,
                variables=list(spec.variables),
                config_json={"blocks_hash": blocks_hash},
                commit_message=(
                    "Turn re-architecture: domain and entity-kind blocks rendered from "
                    "chatbot_domains / chatbot_entity_kinds (AC-1550). Unlabelled - "
                    "promote from the Prompts page."
                ),
            )
        )
        session.commit()
        logger.info(
            "published chatbot parser policy blocks as v%s (%s chars, blocks_hash %s); "
            "production label left where it was",
            next_version,
            len(template),
            blocks_hash[:12],
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upgrade() -> None:
    publish_policy_blocks(op.get_bind())


def downgrade() -> None:
    """Drop the unlabelled version this published. A labelled one is never touched."""
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        labelled = {
            row.version_id
            for row in session.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME)
        }
        for row in session.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME):
            if row.id not in labelled and (row.config_json or {}).get("blocks_hash"):
                session.delete(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
