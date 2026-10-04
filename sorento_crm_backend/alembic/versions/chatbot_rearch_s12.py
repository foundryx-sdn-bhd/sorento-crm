"""chatbot turn re-architecture S12: the hand-set config ships with the deploy

Owner ruling, 21 Sep 2026 (hand pass 12): deploying this lane must need NO hand setup
on prod. Two kinds of state were being carried by hand on the clone and would not have
travelled with the deploy at all.

**Narrowing.** Three values the owner set on the Chatbot Domains page while hand-testing
are the seed now (`app/services/chatbot/turn/policy_rows.py` carries the same three, and
this migration carries them onto an already-seeded database):

  * `order.product`: `optional_filter` -> `list_all`. An order ask naming a product
    lists every variant instead of filtering to one code.
  * `promotion.product`: `optional_filter` -> `narrow_by_tier`.
  * `product_attachment.product`: `must_narrow_one` -> `narrow_by_tier`.

Each is a jsonb `||` merge on that domain's own key, so every other key in the object
(and every other domain) is untouched. All three stay editable on the Chatbot Domains
page; this only moves where the value comes from on a fresh deploy.

**The parser prompt.** `chatbot_rearch_s4` publishes the rendered policy blocks as a NEW
version and deliberately leaves the `production` label where it was, so that promoting
was one reviewed click on the Prompts page. That rule is REVERSED here and for later
publishes in this lane: the blocks are derived from the policy tables, this migration
has just changed three of them, and a deploy that leaves `production` pointing at a
version whose blocks disagree with the tables is a deploy that ships config nobody can
see took effect. So S12 republishes through s4's own `publish_policy_blocks` (never a
second copy of the body formula) and then MOVES the label onto the version whose
template is exactly what today's tables render.

The version id `production` pointed at before the move is recorded on the promoted
version's `config_json` (`s12_prior_production_version_id`), which is what lets
`downgrade()` put the label back on the version it actually came from rather than on
merely some older one. `config_json` is the row's own reserved config slot and the
`template` (the immutable half of the registry contract) is never touched.

Idempotent throughout: the narrowing merges are no-ops on a second run, s4's publish
skips when the full rendered template is already published, and the label move is
skipped (and the prior id therefore not re-recorded) when `production` already points at
the target version.

`downgrade()` restores the three pre-S12 narrowing values and returns the label to the
recorded version. It does NOT delete the version this published: the registry is
append-only, and s4's own `downgrade()` is what removes an unlabelled blocks version if
the chain is unwound that far.

Revision ID: chatbot_rearch_s12
Revises: chatbot_rearch_s11
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

revision = "chatbot_rearch_s12"
down_revision = "chatbot_rearch_s11"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"

# Where `upgrade()` parks the version id `production` pointed at before it moved, so
# `downgrade()` can put it back exactly.
PRIOR_PRODUCTION_KEY = "s12_prior_production_version_id"

# (domain, new value, pre-S12 value) for the one `product` key each row changes.
_NARROWING = (
    ("order", "list_all", "optional_filter"),
    ("promotion", "narrow_by_tier", "optional_filter"),
    ("product_attachment", "narrow_by_tier", "must_narrow_one"),
)


def _load_s4():
    """S4's module, for its publish entrypoint and its body formula.

    Loaded by path because alembic revision files are not importable as a package - the
    same shape `327_scm_coverage_config::_load_311` already uses to reuse a sibling
    migration's own definition instead of restating it. Importing one runs no DDL: every
    migration touches `alembic.op` inside functions only.
    """
    spec = importlib.util.spec_from_file_location(
        "_chatbot_rearch_s12_s4", Path(__file__).resolve().parent / "chatbot_rearch_s4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_narrowing(bind) -> None:
    """Set the three `product` narrowing values.

    Shared by `upgrade()` and `scripts.bootstrap_env.seed_chatbot_policy`, the same
    convention `chatbot_rearch_s6d::apply_narrowing` set: a `create_all`-built database
    gets S0's original seed values and never runs this migration's UPDATE body
    otherwise. Idempotent: a jsonb `||` merge with the same value is a no-op.
    """
    for domain, new_value, _old_value in _NARROWING:
        bind.execute(
            sa.text(
                "UPDATE chatbot_domains "
                "SET narrowing = narrowing || jsonb_build_object('product', :value) "
                "WHERE name = :name"
            ),
            {"value": new_value, "name": domain},
        )


def republish_and_promote(bind) -> None:
    """Republish the parser blocks and point `production` at that version.

    Shared by `upgrade()` and `scripts.bootstrap_env.seed_chatbot_policy` for the same
    reason `apply_narrowing` is, and called AFTER it: s4's publish renders the blocks
    from `chatbot_domains` as they stand at that moment, so the narrowing changes must
    already be in place or the promoted version carries the old three values.
    """
    s4 = _load_s4()
    s4.publish_policy_blocks(bind)

    session = Session(bind=bind)
    try:
        # PLAN-prompt-dynamic-30sep R3: the label moves only by the owner once his
        # wording layer exists; nothing here promotes over it.
        from app.services.chatbot_prompt_vars import wording_layer_exists

        if wording_layer_exists(session):
            logger.info("parser wording layer exists; production label left alone")
            return
        # s4's own formula, imported not copied, so the two can never drift.
        template, _blocks_hash = s4._body(session)
        target = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
            .order_by(AIPromptVersion.version.desc())
            .first()
        )
        if target is None:
            # s4's publish just ran, so this cannot happen on a database it could write
            # to. Log rather than raise: a deploy must not die over a label move.
            logger.warning(
                "chatbot parser blocks not found after publish; production label left alone"
            )
            return

        label = (
            session.query(AIPromptLabel)
            .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
            .first()
        )
        if label is None:
            # `seed_prompt_registry` (inside s4's publish) always creates this row.
            logger.warning("no production label for %s; nothing to promote", PROMPT_NAME)
            return
        if label.version_id == target.id:
            logger.info(
                "chatbot parser v%s is already production; nothing to promote", target.version
            )
            return

        config = dict(target.config_json or {})
        config[PRIOR_PRODUCTION_KEY] = label.version_id
        target.config_json = config
        label.version_id = target.id
        session.commit()
        logger.info(
            "chatbot parser v%s promoted to production (owner ruling 21 Sep 2026: the "
            "deploy ships the config, s4's manual promote step is retired)",
            target.version,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upgrade() -> None:
    bind = op.get_bind()
    apply_narrowing(bind)
    republish_and_promote(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for domain, _new_value, old_value in _NARROWING:
        bind.execute(
            sa.text(
                "UPDATE chatbot_domains "
                "SET narrowing = narrowing || jsonb_build_object('product', :value) "
                "WHERE name = :name"
            ),
            {"value": old_value, "name": domain},
        )

    session = Session(bind=bind)
    try:
        label = (
            session.query(AIPromptLabel)
            .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
            .first()
        )
        if label is None:
            return
        promoted = (
            session.query(AIPromptVersion).filter(AIPromptVersion.id == label.version_id).first()
        )
        if promoted is None:
            return
        config = dict(promoted.config_json or {})
        prior_id = config.pop(PRIOR_PRODUCTION_KEY, None)
        if not prior_id:
            # Never promoted by this migration (or already downgraded once).
            return
        prior = session.query(AIPromptVersion).filter(AIPromptVersion.id == prior_id).first()
        if prior is None:
            logger.warning(
                "the version production pointed at before S12 is gone; label left on v%s",
                promoted.version,
            )
            return
        promoted.config_json = config
        label.version_id = prior.id
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
