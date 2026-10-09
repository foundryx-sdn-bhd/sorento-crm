"""claude.ai connector: permission, users.connector_revoked_at, chat views

Revision ID: acc_0001_connector
Revises: fcom_0003_conv_thread
Create Date: 2026-10-09 00:00:00.000000

CLAUDE-CONNECTOR (card documentation/plans/analytics/CARD-claude-connector-09oct.md; r3 contract
CONTRACT-claude-connector-ss-09oct.md: OAuth, grants and the MCP endpoint live in the shared service).

1. `users.connector_revoked_at`: stamped by `revoke_all_for_user`; a connector call whose consent is
   older is refused with `reconsent_required`.
2. `analytics.connector.use`, granted to NO role (owner ruling Q1 (a): not even admins; the
   connector checks role grants directly).
3. Schema `analytics_connector` with four views, created by
   `app.services.analytics_connector.views.ensure_views` (also run by scripts/bootstrap_env.py).
4. If role `sorento_analytics_ro` exists, it is granted SELECT on the views (the role and its
   password are created by the owner on the server, never in the repo; re-run
   scripts/analytics_ro_grants.sql afterwards if the role came later).
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from app.services.analytics_connector import views

revision = "acc_0001_connector"
down_revision = "fcom_0003_conv_thread"
branch_labels = None
depends_on = None

_PERMISSIONS = (
    (
        "analytics.connector.use",
        "Use the Claude connector",
        "Connect claude.ai to read CRM data as yourself. Chat tools also need View Chat History.",
    ),
)


def upgrade() -> None:
    op.add_column("users", sa.Column("connector_revoked_at", sa.DateTime(), nullable=True))

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

    views.ensure_views(bind)
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": views.RO_ROLE}).scalar():
        views.grant_read_role(bind, views.RO_ROLE)


def downgrade() -> None:
    op.execute(f"DROP SCHEMA IF EXISTS {views.SCHEMA} CASCADE")
    op.execute(
        "DELETE FROM user_permissions WHERE slug IN "
        "('analytics.connector.use')"
    )
    op.drop_column("users", "connector_revoked_at")
