"""Sales module S1 (#1267): the `sales` schema, `sales.reports.view`, the catalog row, and
the chatbot's `crm_sales_analysis` wiring.

PLAN-retail-sales-reports-26sep R4.3 / R5.4 (Owner ruling 26 Sep 07:16 Q5: "we need a sales
schema and a sales module"). #1260 S6 landed first: ``sales_0001_teams`` (this revision's
ancestor) already creates the schema, the teams tables and the catalog row, dormant. So this
revision:

1. runs ``CREATE SCHEMA IF NOT EXISTS sales``, a no-op after ``sales_0001_teams`` (AC-R4-8).
   The plan puts NO table in it (R5.4); #1260's teams tables are the only ones there. ``alembic/env.py`` needs no change: it includes the schemas the
   models declare, and a schema with no model is filtered out of autogenerate.
2. seeds ``sales.reports.view`` and grants it to admin and superadmin (PRINCIPLES DoD 3);
   every other role through the role editor (#1260 3.7).
3. registers the module in ``app_modules_catalog`` (``ON CONFLICT DO NOTHING``, so the row
   ``sales_0001_teams`` wrote stands) and writes NO ``tenant_modules`` row: #1260 shipped
   ``sales`` dormant on purpose, and it stays so (review round 2 S2). The owner switches
   it on in App Store.
4. adds ``crm_sales_analysis`` to the live ``order`` chatbot domain's tool allow-list (never
   ``tools[0]``: the pick is an override in ``run_fetch``), the ``chatbot_rearch_s9`` shape.
5. republishes the parser prompt (its body now teaches ``order_status "sales_analysis"``,
   ``sales_basis`` and ``sales_company``) through s4's own publish and moves the
   ``production`` label onto it, the ``chatbot_rearch_s12`` rule (owner ruling 21 Sep
   2026: the deploy ships the config).

Revision ID: sales_s1_reports_module
Revises: 527_audit_logs_scrub_secrets, spec_0003_rules_null_brand_pol
"""
import importlib.util
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

revision = "sales_s1_reports_module"
# Re-parented onto both main heads at the 27 Sep merge round, so this revision doubles as
# the merge revision (it still descends from sales_0002_team_leader through them).
down_revision = ("527_audit_logs_scrub_secrets", "spec_0003_rules_null_brand_pol")
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

SCHEMA = "sales"
SLUG = "sales.reports.view"
SLUG_NAME = "View sales reports"
SLUG_DESC = (
    "Open the sales reports (Yearly comparison, Sales report), export them and ask for "
    "them on WhatsApp."
)
GRANT_ROLES = ("superadmin", "admin")
TOOL = "crm_sales_analysis"
PROMPT_NAME = "chatbot_semantic_parser"
PRIOR_PRODUCTION_KEY = "sales_s1_prior_production_version_id"


def _load_s4():
    """chatbot_rearch_s4 by path, for its publish entrypoint and body formula (the
    chatbot_rearch_s12 precedent: reuse a sibling migration, never restate it)."""
    spec = importlib.util.spec_from_file_location(
        "_sales_s1_s4", Path(__file__).resolve().parent / "chatbot_rearch_s4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_rbac_and_module(bind) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    bind.execute(
        sa.text(
            "INSERT INTO user_permissions (id, slug, name, description, created_at) "
            "VALUES (:id, :slug, :name, :desc, :now) ON CONFLICT (slug) DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "slug": SLUG, "name": SLUG_NAME, "desc": SLUG_DESC, "now": now},
    )
    bind.execute(
        sa.text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "SELECT gen_random_uuid()::text, r.id, p.id, :now "
            "  FROM user_roles r CROSS JOIN user_permissions p "
            " WHERE r.slug = ANY(:roles) AND p.slug = :slug "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"roles": list(GRANT_ROLES), "slug": SLUG, "now": now},
    )
    bind.execute(
        sa.text(
            "INSERT INTO app_modules_catalog "
            "    (id, module_key, display_name, description, sort_order, is_core, dependencies) "
            "VALUES (:id, 'sales', 'Sales', :desc, '960', false, CAST(:deps AS jsonb)) "
            "ON CONFLICT (module_key) DO NOTHING"
        ),
        {
            "id": str(uuid.uuid4()),
            # The same values `sales_0001_teams` writes, which normally owns the row.
            "desc": "Sales teams, targets with live achievement, opportunities and WhatsApp updates.",
            "deps": '["base", "product", "order"]',
        },
    )


def apply_tools(bind) -> None:
    """The tool joins the order domain's allow-list once. Shared with
    ``scripts.bootstrap_env`` (a create_all database never runs this body)."""
    bind.execute(
        sa.text(
            "UPDATE chatbot_domains "
            "SET tools = array_append(tools, CAST(:tool AS text)) "
            "WHERE name = 'order' AND NOT (CAST(:tool AS text) = ANY(tools))"
        ),
        {"tool": TOOL},
    )


def republish_and_promote(bind) -> None:
    """Republish the parser (constant + today's policy blocks) and point `production`
    at that version, recording where it pointed before (chatbot_rearch_s12's rule)."""
    s4 = _load_s4()
    s4.publish_policy_blocks(bind)
    session = Session(bind=bind)
    try:
        # PLAN-prompt-dynamic-30sep R3: once the owner's wording layer exists the label
        # moves only by the owner; nothing here promotes over it.
        from app.services.chatbot_prompt_vars import wording_layer_exists

        if wording_layer_exists(session):
            logger.info("sales S1: parser wording layer exists; production label left alone")
            return
        template, _blocks_hash = s4._body(session)
        target = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
            .order_by(AIPromptVersion.version.desc())
            .first()
        )
        label = (
            session.query(AIPromptLabel)
            .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
            .first()
        )
        if target is None or label is None:
            logger.warning("sales S1: parser version or production label missing; label left alone")
            return
        if label.version_id == target.id:
            return
        config = dict(target.config_json or {})
        config[PRIOR_PRODUCTION_KEY] = label.version_id
        target.config_json = config
        label.version_id = target.id
        session.commit()
        logger.info("sales S1: chatbot parser v%s promoted to production", target.version)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    seed_rbac_and_module(bind)
    apply_tools(bind)
    republish_and_promote(bind)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE chatbot_domains SET tools = array_remove(tools, CAST(:tool AS text)) "
            "WHERE name = 'order' AND CAST(:tool AS text) = ANY(tools)"
        ),
        {"tool": TOOL},
    )
    session = Session(bind=bind)
    try:
        label = (
            session.query(AIPromptLabel)
            .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
            .first()
        )
        promoted = (
            session.query(AIPromptVersion).filter(AIPromptVersion.id == label.version_id).first()
            if label is not None
            else None
        )
        if promoted is not None:
            config = dict(promoted.config_json or {})
            prior_id = config.pop(PRIOR_PRODUCTION_KEY, None)
            if prior_id:
                promoted.config_json = config
                label.version_id = prior_id
                session.commit()
    finally:
        session.close()
    # The catalog row and any `tenant_modules` row are not this revision's: the first is
    # `sales_0001_teams`'s, the second the owner's App Store switch.
    bind.execute(
        sa.text(
            "DELETE FROM user_role_permissions WHERE permission_id IN "
            "(SELECT id FROM user_permissions WHERE slug = :s)"
        ),
        {"s": SLUG},
    )
    bind.execute(sa.text("DELETE FROM user_permissions WHERE slug = :s"), {"s": SLUG})
    # The schema is left in place: #1260's tables live in it, and a purge never drops a
    # schema (ADR-0011). This plan adds no table to it.
