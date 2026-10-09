"""claude.ai analytics connector: OAuth tables, permissions, session method, chat views

Revision ID: acc_0001_connector
Revises: bflo_0001_borrow_order_toggle
Create Date: 2026-10-09 00:00:00.000000

CLAUDE-CONNECTOR S1 (card documentation/plans/analytics/CARD-claude-connector-09oct.md).

1. Three OAuth tables (clients, pending requests / code store, grants).
2. `analytics.connector.use` and `analytics.connector.manage`, granted to NO role (owner
   ruling Q1 (a): not even admins; the connector guard checks role grants directly).
3. `user_sessions.auth_method` accepts `claude_connector`.
4. Schema `analytics_connector` with four views, created by
   `app.services.analytics_connector.views.ensure_views` (also run by scripts/bootstrap_env.py).
5. If role `sorento_analytics_ro` exists, it is granted SELECT on the views (the role and its
   password are created by the owner on the server, never in the repo; re-run
   scripts/analytics_ro_grants.sql afterwards if the role came later).
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.services.analytics_connector import views

revision = "acc_0001_connector"
down_revision = "bflo_0001_borrow_order_toggle"
branch_labels = None
depends_on = None

_OLD = "('password', 'phone_otp', 'portal_link', 'impersonation', 'dev_login')"
_NEW = "('password', 'phone_otp', 'portal_link', 'impersonation', 'dev_login', 'claude_connector')"

_PERMISSIONS = (
    (
        "analytics.connector.use",
        "Use the Claude connector",
        "Connect claude.ai to read CRM data as yourself. Chat tools also need View Chat History.",
    ),
    (
        "analytics.connector.manage",
        "Manage Claude connector access",
        "List and revoke the Claude connector grants of all staff.",
    ),
)


def upgrade() -> None:
    op.create_table(
        "analytics_oauth_clients",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("client_name", sa.String(200), nullable=True),
        sa.Column("redirect_uris", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "analytics_oauth_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("analytics_oauth_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(100), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("code_hash", sa.String(64), nullable=True),
        sa.Column("code_expires_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_analytics_oauth_requests_code_hash", "analytics_oauth_requests", ["code_hash"])
    op.create_table(
        "analytics_connector_grants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("analytics_oauth_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("previous_refresh_hash", sa.String(64), nullable=True),
        sa.Column("current_session_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_analytics_connector_grants_user_id", "analytics_connector_grants", ["user_id"])
    op.create_index("ix_analytics_connector_grants_refresh_token_hash", "analytics_connector_grants", ["refresh_token_hash"])
    op.create_index("ix_analytics_connector_grants_previous_refresh_hash", "analytics_connector_grants", ["previous_refresh_hash"])

    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for slug, name, desc in _PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO user_permissions (id, slug, name, description, created_at) "
                "VALUES (:id, :slug, :name, :desc, :now) ON CONFLICT (slug) DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "slug": slug, "name": name, "desc": desc, "now": now},
        )

    op.execute("ALTER TABLE user_sessions DROP CONSTRAINT IF EXISTS ck_user_sessions_auth_method")
    op.execute(
        "ALTER TABLE user_sessions ADD CONSTRAINT ck_user_sessions_auth_method "
        f"CHECK (auth_method IN {_NEW})"
    )

    views.ensure_views(bind)
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": views.RO_ROLE}).scalar():
        views.grant_read_role(bind, views.RO_ROLE)


def downgrade() -> None:
    op.execute(f"DROP SCHEMA IF EXISTS {views.SCHEMA} CASCADE")
    op.execute("DELETE FROM user_sessions WHERE auth_method = 'claude_connector'")
    op.execute("ALTER TABLE user_sessions DROP CONSTRAINT IF EXISTS ck_user_sessions_auth_method")
    op.execute(
        "ALTER TABLE user_sessions ADD CONSTRAINT ck_user_sessions_auth_method "
        f"CHECK (auth_method IN {_OLD})"
    )
    op.execute(
        "DELETE FROM user_permissions WHERE slug IN "
        "('analytics.connector.use', 'analytics.connector.manage')"
    )
    op.drop_table("analytics_connector_grants")
    op.drop_table("analytics_oauth_requests")
    op.drop_table("analytics_oauth_clients")
