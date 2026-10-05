"""Chatbot access model: roles -> domains -> fields, one registry (ACCESS-MODEL S1).

Additive. Creates the registry, role, grant and override tables, adds the access columns to
`chatbot_domains`, then seeds: field registry, domain labels and escalation agents, the five
preset roles, and the legacy contacts mapped to roles plus overrides. `reveal_key` becomes NULL
on `inventory` and `order` (they are fields now). The legacy tables are left untouched.

The seeding goes through `app.services.chatbot.access_seed`, which is plain SQL and imports no
ORM model, so a from-scratch upgrade does not break when a model gains a column later.

Revision ID: acm_0001_access_model
Revises: creg_0001_contact_regions
"""
from alembic import op

revision = "acm_0001_access_model"
down_revision = "creg_0001_contact_regions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE chatbot_domains
          ADD COLUMN IF NOT EXISTS access_section text,
          ADD COLUMN IF NOT EXISTS escalation_agent_code text,
          ADD COLUMN IF NOT EXISTS access_group text,
          ADD COLUMN IF NOT EXISTS access_label text,
          ADD COLUMN IF NOT EXISTS access_description text
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_chatbot_domains_escalation_agent') THEN
            ALTER TABLE chatbot_domains ADD CONSTRAINT fk_chatbot_domains_escalation_agent
              FOREIGN KEY (escalation_agent_code) REFERENCES access_agents (code)
              ON UPDATE CASCADE ON DELETE SET NULL;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_domain_fields (
          id uuid PRIMARY KEY,
          domain_name text NOT NULL REFERENCES chatbot_domains (name) ON UPDATE CASCADE ON DELETE CASCADE,
          key text NOT NULL UNIQUE,
          label text NOT NULL,
          kind text NOT NULL DEFAULT 'field',
          prompt_tag text,
          sort_order integer NOT NULL DEFAULT 0,
          CONSTRAINT ck_chatbot_domain_fields_kind CHECK (kind IN ('field', 'report'))
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_chatbot_domain_fields_domain ON chatbot_domain_fields (domain_name)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_roles (
          id uuid PRIMARY KEY,
          code text NOT NULL UNIQUE,
          name text NOT NULL,
          description text,
          sees_all_customers boolean NOT NULL DEFAULT false,
          audience_tier text,
          sort_order integer NOT NULL DEFAULT 0,
          created_at timestamp NOT NULL DEFAULT now(),
          updated_at timestamp NOT NULL DEFAULT now(),
          created_by text,
          updated_by text,
          CONSTRAINT ck_chatbot_roles_audience_tier
            CHECK (audience_tier IS NULL OR audience_tier IN ('dealer', 'office', 'end_user'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_role_domains (
          role_id uuid NOT NULL REFERENCES chatbot_roles (id) ON DELETE CASCADE,
          domain_name text NOT NULL REFERENCES chatbot_domains (name) ON UPDATE CASCADE ON DELETE CASCADE,
          PRIMARY KEY (role_id, domain_name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_role_fields (
          role_id uuid NOT NULL REFERENCES chatbot_roles (id) ON DELETE CASCADE,
          field_key text NOT NULL REFERENCES chatbot_domain_fields (key) ON UPDATE CASCADE ON DELETE CASCADE,
          PRIMARY KEY (role_id, field_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_chatbot_roles (
          contact_id text NOT NULL REFERENCES respond_contacts (id) ON DELETE CASCADE,
          role_id uuid NOT NULL REFERENCES chatbot_roles (id) ON DELETE RESTRICT,
          PRIMARY KEY (contact_id, role_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_access_overrides (
          id uuid PRIMARY KEY,
          contact_id text NOT NULL REFERENCES respond_contacts (id) ON DELETE CASCADE,
          domain_name text NOT NULL REFERENCES chatbot_domains (name) ON UPDATE CASCADE ON DELETE CASCADE,
          field_key text REFERENCES chatbot_domain_fields (key) ON UPDATE CASCADE ON DELETE CASCADE,
          granted boolean NOT NULL,
          created_at timestamp NOT NULL DEFAULT now(),
          updated_at timestamp NOT NULL DEFAULT now(),
          updated_by text
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_contact_access_overrides_contact ON contact_access_overrides (contact_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_access_overrides_domain "
        "ON contact_access_overrides (contact_id, domain_name) WHERE field_key IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_access_overrides_field "
        "ON contact_access_overrides (contact_id, field_key) WHERE field_key IS NOT NULL"
    )

    # Data. Order matters: `inventory` and `order` stop delivering a reveal key (their field rows
    # do), then registry and labels, then roles, then the contacts mapped against that tree.
    from app.services.chatbot import access_seed

    bind = op.get_bind()
    op.execute("UPDATE chatbot_domains SET reveal_key = NULL WHERE name IN ('inventory', 'order')")
    access_seed.seed_domain_labels(bind)
    access_seed.seed_escalation_agents(bind)
    access_seed.seed_field_registry(bind)
    access_seed.seed_default_roles(bind)
    access_seed.map_legacy_contacts(bind)


def downgrade() -> None:
    # The two reveal_key values are restored from the code seed; the legacy tables were never touched.
    op.execute("UPDATE chatbot_domains SET reveal_key = 'inventory.sellable' WHERE name = 'inventory'")
    op.execute("UPDATE chatbot_domains SET reveal_key = 'sales_orders.outstanding' WHERE name = 'order'")
    for table in (
        "contact_access_overrides",
        "contact_chatbot_roles",
        "chatbot_role_fields",
        "chatbot_role_domains",
        "chatbot_roles",
        "chatbot_domain_fields",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("ALTER TABLE chatbot_domains DROP CONSTRAINT IF EXISTS fk_chatbot_domains_escalation_agent")
    for col in ("access_description", "access_label", "access_group", "escalation_agent_code", "access_section"):
        op.execute(f"ALTER TABLE chatbot_domains DROP COLUMN IF EXISTS {col}")
